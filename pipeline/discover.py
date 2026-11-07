#!/usr/bin/env python3
"""Step 1: find every AI-skill repo with >= 10,000 stars via the GitHub Search API.

  GITHUB_TOKEN=... python3 pipeline/discover.py            # writes data/candidates.json
  python3 pipeline/discover.py --max-seconds 150           # stop early; re-run to resume from .cache/search

Runs topic, name/description and README queries plus pipeline/seeds.txt. Any query whose result set would
exceed the Search API's 1,000-result cap is split into star ranges until each slice fits. Results are
deduplicated by repo id, archived repos and forks are dropped, and each repo keeps the list of queries that
found it (`via`), which the classifier uses as a relevance signal.

Safety: if the new candidate list is less than 70% the size of the previous one, the old file is kept and the
script exits 1 (a broken token or API outage shouldn't wipe the site). --force overrides.
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, DATA, MIN_STARS, GitHub, log, read_json, safe_url, sha, write_json

TOPICS = ['claude-skills', 'agent-skills', 'mcp', 'mcp-server', 'model-context-protocol', 'ai-agents', 'ai-agent',
          'llm-tools', 'prompt-engineering', 'prompts', 'cursor-rules', 'claude-code', 'langchain', 'autonomous-agents',
          'agents', 'llm-agent', 'multi-agent', 'agentic-ai', 'agent', 'rag', 'computer-use', 'browser-automation',
          'coding-agent', 'ai-coding', 'llmops', 'llm-evaluation', 'agent-framework', 'chatgpt-prompts', 'awesome-mcp']
NAME_DESC = ['agent', 'agents', 'agentic', 'mcp', '"model context protocol"', 'skills', 'prompts', 'claude',
             '"coding assistant"', 'cursorrules', '"awesome llm"', '"awesome ai"', '"awesome chatgpt"', 'copilot', 'rag']
README = ['"Model Context Protocol"', '"SKILL.md"', '"Claude Code"', '"AGENTS.md"', '"cursorrules"', '"agent skills"']


def queries():
    qs = [('topic', t, f'topic:{t}') for t in TOPICS]
    qs += [('name', k.strip('"'), f'{k} in:name,description') for k in NAME_DESC]
    qs += [('readme', k.strip('"'), f'{k} in:readme') for k in README]
    return qs


def read_seeds(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.split('#', 1)[0].strip()
            if line and '/' in line:
                out.append(line)
    return sorted(set(out), key=out.index)


def normalize(item):
    lic = item.get('license') or {}
    return {
        'id': item['id'], 'full_name': item['full_name'], 'html_url': safe_url(item.get('html_url')) or f"https://github.com/{item['full_name']}",
        'description': (item.get('description') or '').strip(), 'stars': item.get('stargazers_count', 0),
        'forks': item.get('forks_count', 0), 'language': item.get('language'),
        'license': lic.get('spdx_id') if lic.get('spdx_id') not in (None, 'NOASSERTION') else (lic.get('name') or None),
        'topics': item.get('topics') or [], 'created_at': item.get('created_at'), 'pushed_at': item.get('pushed_at'),
        'homepage': safe_url(item.get('homepage')), 'archived': bool(item.get('archived')), 'fork': bool(item.get('fork')),
        'default_branch': item.get('default_branch') or 'main',
    }


def rest_repo(gh, full_name):
    """GET /repos/{owner}/{name}. GitHub answers renamed/transferred repos with a redirect, which requests follows,
    so this finds seeds whose name changed. Archived/forked seeds are filtered later by keep()."""
    r = gh.get(f'/repos/{full_name}')
    if r.status_code in (404, 451):
        return None
    r.raise_for_status()
    return normalize(r.json())


def keep(repo, min_stars=MIN_STARS):
    return repo['stars'] >= min_stars and not repo['archived'] and not repo['fork']


def merge(found, repo, via):
    """Deduplicate by repo id. Later sightings refresh metadata (renames keep the same id) and add to `via`."""
    cur = found.get(repo['id'])
    if cur is None:
        found[repo['id']] = dict(repo, via=[via])
    else:
        vias = cur['via'] + ([via] if via not in cur['via'] else [])
        cur.update(repo); cur['via'] = vias
    return found


class Searcher:
    def __init__(self, gh, ttl_hours=20, deadline=None):
        self.gh, self.ttl, self.deadline = gh, ttl_hours * 3600, deadline
        self.cdir = os.path.join(CACHE, 'search'); os.makedirs(self.cdir, exist_ok=True)

    def page(self, q, page):
        key = os.path.join(self.cdir, sha(f'{q}|{page}') + '.json')
        hit = read_json(key)
        if hit and time.time() - hit['t'] < self.ttl:
            return hit['data']
        if self.deadline and time.time() > self.deadline:
            raise TimeoutError
        r = self.gh.get('/search/repositories', params={'q': q, 'sort': 'stars', 'order': 'desc', 'per_page': 100, 'page': page})
        if r.status_code == 422:
            log(f'  422 for {q!r}: {r.text[:160]}'); data = {'total_count': 0, 'items': [], 'incomplete_results': False}
        else:
            r.raise_for_status(); data = r.json()
        write_json(key, {'t': time.time(), 'q': q, 'page': page, 'data': data})
        return data

    def search(self, base, lo=MIN_STARS, hi=None):
        """All results for `base` within stars lo..hi, splitting the range while a slice exceeds 1,000 results."""
        rng = f'stars:>={lo}' if hi is None else f'stars:{lo}..{hi}'
        q = f'{base} {rng} archived:false'
        first = self.page(q, 1)
        total = first.get('total_count', 0)
        if total > 1000 and (hi is None or hi > lo):
            mid = lo * 2 - 1 if hi is None else (lo + hi) // 2
            log(f'  {total} results for {q!r}; splitting at {mid}')
            return self.search(base, lo, mid) + self.search(base, mid + 1, hi)
        items = list(first.get('items', []))
        for p in range(2, min(10, -(-min(total, 1000) // 100)) + 1):
            items += self.page(q, p).get('items', [])
        return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(DATA, 'candidates.json'))
    ap.add_argument('--seeds', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seeds.txt'))
    ap.add_argument('--max-seconds', type=float, default=0, help='stop early (exit 3) and resume on the next run')
    ap.add_argument('--force', action='store_true', help='write even if the result shrank by more than 30%%')
    a = ap.parse_args()

    gh = GitHub()
    log('GitHub token:', 'yes' if gh.token else 'no (10 searches/min; set GITHUB_TOKEN for 30/min)')
    s = Searcher(gh, deadline=time.time() + a.max_seconds if a.max_seconds else None)
    found, per_query = {}, {}
    try:
        for kind, label, q in queries():
            items = s.search(q)
            n0 = len(found)
            for it in items:
                merge(found, normalize(it), f'{kind}:{label}')
            per_query[f'{kind}:{label}'] = len(items)
            log(f'{kind:6} {label:28} {len(items):4} hits, {len(found) - n0:3} new, {len(found)} total')
        seeds = read_seeds(a.seeds)
        seen_names = {r['full_name'].lower() for r in found.values()}
        todo = [x for x in seeds if x.lower() not in seen_names]
        for i in range(0, len(todo), 12):
            chunk = todo[i:i + 12]
            items = s.search(' '.join(f'repo:{x}' for x in chunk), lo=0)
            for it in items:
                merge(found, normalize(it), 'seed')
            log(f'seeds  {len(chunk)} looked up, {len(items)} found')
        seen_names = {r['full_name'].lower() for r in found.values()}
        for x in [x for x in todo if x.lower() not in seen_names]:
            repo = rest_repo(gh, x)
            if repo is None:
                log(f'seeds  {x}: not found (deleted or private?); remove it from seeds.txt')
                continue
            if repo['full_name'].lower() != x.lower():
                log(f'seeds  {x} is now {repo["full_name"]}; update seeds.txt')
            merge(found, repo, 'seed')
        for r in found.values():                 # repos already found by a query that are also seeds
            if r['full_name'].lower() in {x.lower() for x in seeds} and 'seed' not in r['via']:
                r['via'].append('seed')
    except TimeoutError:
        log(f'time budget reached after {gh.calls} API calls; re-run to resume (cached pages are reused)')
        sys.exit(3)

    repos = sorted((r for r in found.values() if keep(r)), key=lambda r: (-r['stars'], r['full_name'].lower()))
    dropped = len(found) - len(repos)
    prev = read_json(a.out, {}) or {}
    prev_n = len(prev.get('repos', []))
    if prev_n and len(repos) < 0.7 * prev_n and not a.force:
        log(f'ABORT: {len(repos)} candidates vs {prev_n} last time; keeping the previous file (use --force to override)')
        sys.exit(1)
    write_json(a.out, {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'min_stars': MIN_STARS,
                       'queries': per_query, 'repos': repos})
    log(f'{len(repos)} candidates with >= {MIN_STARS} stars ({dropped} below threshold/archived/fork dropped), '
        f'{gh.calls} API calls -> {os.path.relpath(a.out)}')


if __name__ == '__main__':
    main()
