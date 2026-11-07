#!/usr/bin/env python3
"""Step 3: give every repo one category, up to 3 tags, a one-line summary and an in-scope flag.

  ANTHROPIC_API_KEY=... python3 pipeline/classify.py      # Claude Haiku via the Message Batches API
  python3 pipeline/classify.py --dry-run                   # show what would be sent and the cost estimate
  python3 pipeline/classify.py                             # no key: keyword rules from taxonomy.py

Cache: data/labels/labels.json, keyed by repo id; an LLM label is reused while the repo's README hash (and the
taxonomy version) is unchanged, so a weekly run only sends new or changed repos (typically cents).
Repos the LLM hasn't labeled yet get keyword-rule labels, which are upgraded as soon as a key is available.
If a batch is still running when --max-wait expires, its id is saved in data/labels/batch_state.json and the
next run collects it first; meanwhile those repos keep their previous (or rule) labels.
"""
import argparse, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, log, read_json, read_jsonl, write_json
from taxonomy import CATEGORIES, CATEGORY_IDS, TAXONOMY_VERSION, rule_classify

API = 'https://api.anthropic.com/v1/messages/batches'
MODEL = os.environ.get('SS_LABEL_MODEL', 'claude-haiku-4-5')
LABELS = os.path.join(DATA, 'labels', 'labels.json')
STATE = os.path.join(DATA, 'labels', 'batch_state.json')
PRICE_IN, PRICE_OUT = 0.50, 2.50            # $/M tokens, Haiku 4.5 at the 50% batch discount

SYSTEM = f"""You catalog GitHub repositories for SkillStars, a directory of "AI skill repos": repos that give AI agents or
LLM coding tools reusable capabilities. In scope: Claude Code skills, Agent Skills (SKILL.md), MCP servers/clients/SDKs,
prompt and rule collections (system prompts, .cursorrules, AGENTS.md), agent frameworks and platforms, AI coding agents,
tool/plugin collections for agents, browser/computer-use agents, RAG and agent memory, evals/observability for LLM apps,
and awesome-lists about these. Out of scope: general software that merely mentions AI tools in its README, model
training/inference engines, model weights, generic ML libraries, chat UIs with no agent/tool capability, non-AI repos.

Categories (use the id):
""" + '\n'.join(f'- {cid}: {label}. {d}' for cid, label, d in CATEGORIES) + """

Answer with only a JSON object:
{"in_scope": true|false, "category": "<id>", "tags": ["up to 3 short lowercase-hyphenated tags, e.g. claude, browser, multi-agent"],
 "summary": "<one plain-English sentence, at most 20 words, saying what it is and who it is for; no hype, no emoji>"}"""


def prompt(r):
    return (f"Repository: {r['full_name']}\nDescription: {r.get('description') or ''}\nTopics: {', '.join(r.get('topics') or [])}\n"
            f"Language: {r.get('language') or ''}\nStars: {r['stars']}\n\nREADME (first 3 KB):\n{r.get('readme') or '(none)'}")


def cache_key(r):
    return f"{r['id']}:{r.get('readme_hash') or ''}:v{TAXONOMY_VERSION}"


def slug(t):
    return re.sub(r'[^a-z0-9]+', '-', str(t).lower()).strip('-')[:24]


def clean_label(obj, repo):
    """Validate an LLM answer; anything malformed falls back to the rules for that field."""
    rule = rule_classify(repo)
    cat = obj.get('category') if obj.get('category') in CATEGORY_IDS else rule['category']
    tags = []
    for t in obj.get('tags') or []:
        t = slug(t)
        if t and t != cat and t not in tags:
            tags.append(t)
    summary = re.sub(r'\s+', ' ', str(obj.get('summary') or '')).strip() or rule['summary']
    if len(summary) > 180:
        summary = summary[:177].rsplit(' ', 1)[0] + '…'
    scope = obj.get('in_scope')
    return {'category': cat, 'tags': tags[:3], 'summary': summary,
            'in_scope': scope if isinstance(scope, bool) else rule['in_scope']}


def parse_answer(text):
    try:
        return json.loads(text[text.index('{'): text.rindex('}') + 1])
    except ValueError:
        return None


RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
STATE_MAX_AGE = 28 * 86400       # batch results are deleted 29 days after creation
STATE_MAX_ERRORS = 3             # give up on a pending batch after this many runs that couldn't read it


class BatchGone(Exception):
    """The saved batch can't be collected any more (deleted, expired, belongs to another key)."""


def call(method, url, key, body=None, tries=5, sleep=time.sleep):
    """Anthropic API call with retries on network errors, 429, 5xx and 529 (overloaded), honoring retry-after."""
    import requests
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            r = requests.request(method, url, json=body, timeout=120,
                                 headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        except requests.RequestException as e:
            if last:
                raise
            log(f'Anthropic API network error ({e.__class__.__name__}); retrying')
            sleep(min(60, 5 * 2 ** attempt)); continue
        if r.status_code in RETRY_STATUS and not last:
            wait = float(r.headers.get('retry-after') or min(60, 5 * 2 ** attempt))
            log(f'Anthropic API HTTP {r.status_code}; retrying in {wait:.0f}s')
            sleep(wait); continue
        if r.status_code == 404 and '/batches/' in url:
            raise BatchGone(url)
        r.raise_for_status()
        return r


def state_stale(state, now=None):
    """True when a saved batch should be abandoned (and its repos resubmitted)."""
    now = now or time.time()
    return (not state or not state.get('batch_id') or now - state.get('submitted_at', 0) > STATE_MAX_AGE
            or state.get('errors', 0) >= STATE_MAX_ERRORS)


def collect(key, state, repos_by_id, labels, deadline, sleep=time.sleep):
    """Wait (until the absolute `deadline`, shared by the whole run) for a submitted batch and merge its results.
    Returns True when collected, False when it's still running (it stays in batch_state.json for the next run)."""
    while True:
        b = call('GET', f"{API}/{state['batch_id']}", key).json()
        log('batch', state['batch_id'], b['processing_status'], b.get('request_counts'))
        if b['processing_status'] == 'ended':
            if not b.get('results_url'):
                raise BatchGone(f"{state['batch_id']} ended without results")
            break
        left = deadline - time.time()
        if left <= 0:
            log('batch still running; it will be collected on the next run')
            return False
        sleep(min(30, left))
    good = bad = 0
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    for line in call('GET', b['results_url'], key).text.splitlines():
        res = json.loads(line)
        rid = res['custom_id'].split('-', 1)[1]
        repo = repos_by_id.get(rid)
        obj = None
        if res['result']['type'] == 'succeeded':
            obj = parse_answer(''.join(c.get('text', '') for c in res['result']['message']['content']))
        if repo is None or obj is None:
            bad += 1; continue
        labels[rid] = dict(clean_label(obj, repo), key=state['keys'].get(rid, cache_key(repo)), source='llm', model=state['model'], labeled_at=now)
        good += 1
    log(f'LLM labels: {good} ok, {bad} failed (failures keep rule labels and are retried next run)')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--repos', default=os.path.join(DATA, 'repos.jsonl'))
    ap.add_argument('--labels', default=LABELS)
    ap.add_argument('--state', default=STATE)
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--max-wait', type=float, default=float(os.environ.get('SS_CLASSIFY_MAX_WAIT', 30 * 60)),
                    help='total seconds this run may spend waiting on batches (pending + new), default 1800')
    ap.add_argument('--prune-days', type=int, default=180, help='forget labels of repos unseen this long')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--rules-only', action='store_true')
    a = ap.parse_args(argv)
    deadline = time.time() + a.max_wait          # one budget for the whole run, so CI never waits 2 x max-wait

    repos = read_jsonl(a.repos) or sys.exit('no repos; run enrich.py first')
    by_id = {str(r['id']): r for r in repos}
    labels = read_json(a.labels, {}) or {}
    key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    use_llm = bool(key) and not a.rules_only and not a.dry_run

    def drop_state(why):
        log(f'abandoning saved batch ({why}); its repos will be resubmitted')
        try:
            os.remove(a.state)
        except FileNotFoundError:
            pass

    if use_llm and os.path.exists(a.state):                   # finish last run's batch first
        state = read_json(a.state) or {}
        if state_stale(state):
            drop_state('expired, unreadable or failing repeatedly')
        else:
            try:
                if collect(key, state, by_id, labels, deadline):
                    os.remove(a.state)
            except BatchGone as e:
                drop_state(f'gone: {e}')
            except Exception as e:
                state['errors'] = state.get('errors', 0) + 1
                write_json(a.state, state)
                log(f'could not collect pending batch ({e}); attempt {state["errors"]}/{STATE_MAX_ERRORS}')
        write_json(a.labels, labels, indent=1)

    todo = [r for r in repos if not (labels.get(str(r['id'])) or {}).get('source') == 'llm'
            or labels[str(r['id'])].get('key') != cache_key(r)]
    est_in = sum(len(SYSTEM) + len(prompt(r)) for r in todo) / 3.6
    est_out = 70 * len(todo)
    log(f'{len(repos)} repos, {len(repos) - len(todo)} cached LLM labels, {len(todo)} to label '
        f'(~{est_in / 1e3:.0f}K in / {est_out / 1e3:.0f}K out tokens, ~${(est_in * PRICE_IN + est_out * PRICE_OUT) / 1e6:.2f} with {a.model} batches)')
    if a.dry_run:
        print('--- system ---\n' + SYSTEM + '\n--- first request ---\n' + (prompt(todo[0]) if todo else '(nothing to do)'))
        return labels

    if use_llm and todo and not os.path.exists(a.state):
        try:
            reqs = [{'custom_id': f"r-{r['id']}", 'params': {'model': a.model, 'max_tokens': 300, 'system': SYSTEM,
                     'messages': [{'role': 'user', 'content': prompt(r)}]}} for r in todo]
            bid = call('POST', API, key, {'requests': reqs}).json()['id']
            state = {'batch_id': bid, 'model': a.model, 'submitted_at': time.time(), 'errors': 0,
                     'keys': {str(r['id']): cache_key(r) for r in todo}}
            write_json(a.state, state)
            log(f'submitted batch {bid} with {len(reqs)} requests')
            if collect(key, state, by_id, labels, deadline):
                os.remove(a.state)
        except Exception as e:
            log(f'LLM labeling failed ({e}); using keyword rules for unlabeled repos')
    elif not key:
        log('ANTHROPIC_API_KEY not set: keyword rules for repos without a cached LLM label')

    # Repos without an LLM label get a fresh rule label. LLM labels are never discarded just because the README
    # changed or no key is set this run: a slightly stale LLM label beats a rule label.
    today = time.strftime('%Y-%m-%d', time.gmtime())
    n_rules = 0
    for r in repos:
        rid = str(r['id'])
        if (labels.get(rid) or {}).get('source') != 'llm':
            labels[rid] = dict(rule_classify(r), key=cache_key(r), source='rules', model=None, labeled_at=None)
            n_rules += 1
        labels[rid]['last_seen'] = today
    # Keep labels of repos that dropped out of this run (a dip below 10K stars, a flaky search) so they come back
    # without paying for relabeling; forget them only after --prune-days.
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - a.prune_days * 86400))
    labels = {rid: v for rid, v in labels.items() if rid in by_id or (v.get('last_seen') or today) >= cutoff}
    write_json(a.labels, labels, indent=1)
    cur = [labels[rid] for rid in by_id]
    n_scope = sum(1 for v in cur if v['in_scope'])
    log(f'{len(cur)} current labels ({len(cur) - n_rules} LLM, {n_rules} rules), {len(labels) - len(cur)} retained for absent repos; '
        f'{n_scope} in scope -> {os.path.relpath(a.labels)}')
    return labels


if __name__ == '__main__':
    main()
