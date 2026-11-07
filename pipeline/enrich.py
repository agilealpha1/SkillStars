#!/usr/bin/env python3
"""Step 2: add the first 3 KB of each candidate's README (and fresh counts) -> data/repos.jsonl.

  python3 pipeline/enrich.py

With GITHUB_TOKEN: one GraphQL query per 25 repos returns stars, forks, push time and the README blob
(trying the common file names as aliases), instead of 2+ REST calls per repo.
Without a token (GraphQL is unavailable): README files come from raw.githubusercontent.com, which does not
count against the 60/hour anonymous REST quota.

READMEs are reused from the previous run when a repo's pushed_at hasn't changed, so a weekly run only
downloads READMEs for new or recently pushed repos. A README that fails to download keeps its old copy.
"""
import argparse, concurrent.futures as cf, json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, GitHub, log, read_json, read_jsonl, sha, write_jsonl

README_BYTES = 3072
RANGE_BYTES = 4096        # ask raw.githubusercontent.com for a bit more than we keep, then cut on a char boundary
_tls = threading.local()
NAMES = ['README.md', 'readme.md', 'Readme.md', 'README.MD', 'README.rst', 'README', 'README.markdown', 'README.txt']
FIELDS = ['id', 'full_name', 'html_url', 'description', 'stars', 'forks', 'language', 'license', 'topics',
          'created_at', 'pushed_at', 'homepage', 'default_branch', 'via']


def clip(text, n=README_BYTES):
    """First n bytes of text, cut on a UTF-8 character boundary."""
    b = (text or '').encode('utf-8')[:n]
    return b.decode('utf-8', errors='ignore')


def gql_batch(gh, repos):
    parts = []
    for i, r in enumerate(repos):
        owner, name = r['full_name'].split('/', 1)
        blobs = ' '.join(f'f{j}: object(expression: {json.dumps("HEAD:" + n)}) {{ ... on Blob {{ text }} }}' for j, n in enumerate(NAMES[:6]))
        parts.append(f'r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
                     f'{{ databaseId stargazerCount forkCount pushedAt isArchived {blobs} }}')
    data = gh.graphql('query { ' + ' '.join(parts) + ' }')
    out = {}
    for i, r in enumerate(repos):
        d = data.get(f'r{i}')
        if not d:
            continue
        text = next((d[f'f{j}']['text'] for j in range(6) if d.get(f'f{j}') and d[f'f{j}'].get('text')), None)
        out[r['id']] = {'stars': d['stargazerCount'], 'forks': d['forkCount'], 'pushed_at': d['pushedAt'],
                        'archived': d['isArchived'], 'readme': text}
    return out


def session():
    """One requests.Session per worker thread (Session isn't thread-safe). Range keeps downloads at 4 KB even for
    huge READMEs; identity encoding makes the byte range apply to the file itself, not to a gzip stream."""
    s = getattr(_tls, 's', None)
    if s is None:
        import requests
        s = requests.Session()
        s.headers.update({'User-Agent': 'SkillStars-pipeline', 'Range': f'bytes=0-{RANGE_BYTES - 1}', 'Accept-Encoding': 'identity'})
        _tls.s = s
    return s


def raw_readme(full_name, branch):
    for ref in dict.fromkeys(['HEAD', branch]):
        for n in NAMES:
            for attempt in range(3):
                try:
                    r = session().get(f'https://raw.githubusercontent.com/{full_name}/{ref}/{n}', timeout=30)
                except Exception:
                    time.sleep(2 * (attempt + 1)); continue
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(float(r.headers.get('Retry-After') or 5 * (attempt + 1))); continue
                break
            else:
                continue
            if r.status_code in (200, 206):
                text = r.content.decode('utf-8', errors='ignore')
                if text.strip():
                    return text
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--candidates', default=os.path.join(DATA, 'candidates.json'))
    ap.add_argument('--out', default=os.path.join(DATA, 'repos.jsonl'))
    ap.add_argument('--refresh-all', action='store_true', help='re-download every README')
    a = ap.parse_args()

    cands = (read_json(a.candidates) or {}).get('repos') or sys.exit('no candidates; run discover.py first')
    prev = {r['id']: r for r in read_jsonl(a.out)}
    todo = [c for c in cands if a.refresh_all or c['id'] not in prev or prev[c['id']].get('pushed_at') != c['pushed_at']
            or prev[c['id']].get('readme') is None]
    log(f'{len(cands)} candidates; {len(todo)} need a README fetch, {len(cands) - len(todo)} reused')

    gh, fresh = GitHub(), {}
    if gh.token and todo:
        for i in range(0, len(todo), 25):
            try:
                fresh.update(gql_batch(gh, todo[i:i + 25]))
            except Exception as e:                          # fall through to raw downloads for this chunk
                log(f'GraphQL chunk {i // 25} failed: {e}')
        log(f'GraphQL: {len(fresh)} repos in {-(-len(todo) // 25)} queries')
    missing = [c for c in todo if not (fresh.get(c['id']) or {}).get('readme')]
    if missing:
        with cf.ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(raw_readme, c['full_name'], c.get('default_branch') or 'main'): c for c in missing}
            for f in cf.as_completed(futs):
                c = futs[f]
                fresh.setdefault(c['id'], {})['readme'] = f.result()
        log(f'raw.githubusercontent.com: {sum(1 for c in missing if fresh[c["id"]].get("readme"))}/{len(missing)} READMEs')

    rows, now, failed = [], time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 0
    for c in cands:
        row = {k: c.get(k) for k in FIELDS}
        old, new = prev.get(c['id'], {}), fresh.get(c['id'], {})
        if new.get('archived'):
            continue                                         # archived since discovery
        for k in ('stars', 'forks', 'pushed_at'):
            if new.get(k) is not None:
                row[k] = new[k]
        if new.get('readme'):
            row['readme'], row['readme_fetched_at'] = clip(new['readme']), now
        else:
            row['readme'], row['readme_fetched_at'] = old.get('readme'), old.get('readme_fetched_at')
            failed += c in todo
        row['readme_hash'] = sha(row['readme'] or '')
        rows.append(row)
    write_jsonl(a.out, rows)
    log(f'{len(rows)} repos -> {os.path.relpath(a.out)} ({failed} READMEs unavailable, kept previous/none)')


if __name__ == '__main__':
    main()
