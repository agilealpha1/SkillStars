#!/usr/bin/env python3
"""Step 5: build the static site into site_dist/.

  python3 pipeline/build_site_data.py [--out site_dist]

Writes
  index.html                       copied from site/index.html (the whole app; relative paths, works under /SkillStars/)
  data/index.json                  slim list of every in-scope repo (what the home page loads)
  data/r/<owner>__<name>.json      one file per repo: README excerpt, star history, similar repos
  data/categories.json             the taxonomy with counts
  data/stats.json                  totals, snapshot dates, label sources
  feed.xml                         RSS of repos that newly crossed into the catalog
  download/skillstars-data.zip     CSV + JSON + star history, CC0

The site is built in a temporary sibling directory, validated (validate()), and only then swapped in, so a
failed build leaves the previous site_dist/ untouched.
"""
import argparse, csv, datetime as dt, html, io, json, os, re, shutil, sys, time, zipfile
from email.utils import format_datetime
from xml.sax.saxutils import escape as xesc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, MIN_STARS, ROOT, log, read_json, read_jsonl, safe_url
from taxonomy import CATEGORIES, CATEGORY_IDS, LABELS, rule_classify

OUT = os.path.join(ROOT, 'site_dist')
SCHEMA_VERSION = 1
INDEX_FIELDS = {'id': int, 'name': str, 'slug': str, 'url': str, 'summary': str, 'category': str, 'tags': list, 'topics': list,
                'stars': int, 'gain': (int, type(None)), 'forks': int, 'language': (str, type(None)),
                'license': (str, type(None)), 'created': str, 'pushed': str, 'new': bool, 'src': str}
DETAIL_FIELDS = {'description': str, 'homepage': (str, type(None)), 'topics': list, 'readme': str,
                 'readme_truncated': bool, 'history': list, 'similar': list, 'first_seen': (str, type(None))}
ZIP_MEMBERS = {'skillstars.csv', 'skillstars.json', 'categories.json', 'stars-history.jsonl', 'LICENSE.txt', 'README.txt'}
CSV_COLUMNS = ['full_name', 'url', 'category', 'category_label', 'tags', 'summary', 'description', 'stars', 'weekly_gain',
               'forks', 'language', 'license', 'created_at', 'pushed_at', 'homepage', 'first_seen', 'label_source']


def slug_of(full_name):
    return re.sub(r'[^A-Za-z0-9._-]', '-', full_name.replace('/', '__'))


def _clean_text(t):
    t = re.sub(r'<!--.*?-->', '', t, flags=re.S)
    t = re.sub(r'\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)', '', t)           # linked badges
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', t)                          # images
    t = re.sub(r'<(script|style)\b.*?</\1>', '', t, flags=re.S | re.I)
    t = re.sub(r'</?(p|div|h[1-6]|br|li|tr|table|details|summary)\b[^>]*>', '\n', t, flags=re.I)
    t = re.sub(r'</?[a-zA-Z][^>]*>', '', t)                             # remaining tags
    t = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', t)                      # [text](url) -> text
    t = re.sub(r'^\s*\[[^\]]+\]:\s*\S+.*$', '', t, flags=re.M)          # reference definitions
    t = html.unescape(t)
    out, blank = [], 0
    for l in (l.rstrip() for l in t.splitlines()):
        if not l.strip() or re.fullmatch(r'\s*[-=*_|: ]{3,}\s*', l):
            blank += 1
            if blank == 1 and out:
                out.append('')
            continue
        blank = 0
        out.append(l)
    return '\n'.join(out).strip()


def clean_readme(md):
    """Markdown/HTML README excerpt -> readable plain-ish text. Headings and lists survive; fenced code blocks are
    kept verbatim (so `<Component />`, `Map<K, V>` or `---` inside code aren't stripped). An unclosed fence (the
    3 KB cut often lands inside one) is treated as code to the end."""
    parts, buf, fence = [], [], None
    for line in (md or '').splitlines():
        m = re.match(r'^\s*(`{3,}|~{3,})', line)
        if m and fence is None:
            parts.append(('text', buf)); buf, fence = [line], m.group(1)[0] * 3
        elif m and line.strip().startswith(fence):
            buf.append(line); parts.append(('code', buf)); buf, fence = [], None
        else:
            buf.append(line)
    parts.append(('code' if fence else 'text', buf))
    out = []
    for kind, lines in parts:
        text = '\n'.join(lines) if kind == 'code' else _clean_text('\n'.join(lines))
        if text.strip():
            out.append(text.rstrip())
    return '\n'.join(out).strip('\n')


def history_index(history):
    """id -> list of [date, stars]; plus first-seen date per id."""
    series, first = {}, {}
    for h in history:
        for rid, stars in h['stars'].items():
            series.setdefault(rid, []).append([h['date'], stars])
            first.setdefault(rid, h['date'])
    return series, first


def similar_repos(items, k=6):
    """Similar = shares tags (2 points each) + same category (1 point); topped up with the category's biggest repos."""
    by_tag = {}
    for r in items:
        for t in r['tags']:
            by_tag.setdefault(t, []).append(r)
    out = {}
    for r in items:
        score = {}
        for t in r['tags']:
            for o in by_tag[t]:
                if o['id'] != r['id']:
                    score[o['id']] = score.get(o['id'], 0) + 2
        cands = [o for o in items if o['id'] in score]
        for o in cands:
            score[o['id']] += o['category'] == r['category']
        ranked = sorted(cands, key=lambda o: (-score[o['id']], -o['stars']))[:k]
        if len(ranked) < 3:
            ranked += [o for o in items if o['category'] == r['category'] and o['id'] != r['id'] and o not in ranked][:3 - len(ranked)]
        out[r['id']] = [o['slug'] for o in ranked]
    return out


def rss(items, base, generated):
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', '<channel>',
           '<title>SkillStars: new over 10K stars</title>', f'<link>{xesc(base)}</link>',
           f'<atom:link href="{xesc(base)}feed.xml" rel="self" type="application/rss+xml"/>',
           '<description>AI agent skill repos (skills, MCP servers, agent frameworks, prompt and rule collections) newly over 10,000 GitHub stars.</description>',
           '<language>en</language>', f'<lastBuildDate>{format_datetime(generated)}</lastBuildDate>']
    for r in items:
        when = format_datetime(dt.datetime.fromisoformat(r['first_seen']).replace(tzinfo=dt.timezone.utc))
        out += ['<item>', f"<title>{xesc(r['name'])} ({r['stars']:,} stars)</title>",
                f"<link>{xesc(base)}#/r/{xesc(r['name'])}</link>", f"<guid isPermaLink=\"false\">skillstars-repo-{r['id']}</guid>",
                f'<pubDate>{when}</pubDate>', f"<category>{xesc(LABELS[r['category']])}</category>",
                f"<description>{xesc(r['summary'])} ({xesc(r['url'])})</description>", '</item>']
    return '\n'.join(out + ['</channel>', '</rss>']) + '\n'


def data_zip(items, details, cats, history_path, generated):
    buf, stamp = io.BytesIO(), generated.timetuple()[:6]
    rows = []
    for r in items:
        d = details[r['id']]
        rows.append({'full_name': r['name'], 'url': r['url'], 'category': r['category'], 'category_label': LABELS[r['category']],
                     'tags': ';'.join(r['tags']), 'summary': r['summary'], 'description': d['description'], 'stars': r['stars'],
                     'weekly_gain': '' if r['gain'] is None else r['gain'], 'forks': r['forks'], 'language': r['language'] or '',
                     'license': r['license'] or '', 'created_at': r['created'], 'pushed_at': r['pushed'],
                     'homepage': d['homepage'] or '', 'first_seen': d['first_seen'] or '', 'label_source': r['src']})
    s = io.StringIO()
    w = csv.DictWriter(s, CSV_COLUMNS, lineterminator='\n'); w.writeheader(); w.writerows(rows)
    history = ''
    if os.path.exists(history_path):
        with open(history_path, encoding='utf-8') as f:
            history = f.read()
    as_json = [dict(x, tags=x['tags'].split(';') if x['tags'] else [], weekly_gain=None if x['weekly_gain'] == '' else x['weekly_gain'])
               for x in rows]
    files = {
        'skillstars.csv': s.getvalue(),
        'skillstars.json': json.dumps(as_json, ensure_ascii=False, indent=1),
        'categories.json': json.dumps(cats, ensure_ascii=False, indent=1),
        'stars-history.jsonl': history,
        'LICENSE.txt': 'CC0 1.0 Universal (public domain dedication)\n\nTo the extent possible under law, the SkillStars maintainers have '
                       'waived all copyright and related rights to this dataset.\nhttps://creativecommons.org/publicdomain/zero/1.0/\n\n'
                       'Repository names, descriptions and README excerpts belong to their authors; the dataset only catalogs public GitHub metadata.\n',
        'README.txt': f"SkillStars open data, generated {generated:%Y-%m-%d %H:%M} UTC.\n\n"
                      f"skillstars.csv / skillstars.json  one row per repo ({len(rows)} repos with >= {MIN_STARS:,} stars)\n"
                      "categories.json                   the 10-category taxonomy with counts\n"
                      "stars-history.jsonl               one line per weekly snapshot: {week, date, stars: {github_repo_id: stars}}\n\n"
                      "label_source is 'llm' (Claude Haiku) or 'rules' (keyword fallback). weekly_gain is empty until two snapshots exist.\n",
    }
    with zipfile.ZipFile(buf, 'w') as z:
        for name, text in files.items():
            zi = zipfile.ZipInfo(name, stamp); zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, text)
    return buf.getvalue()


def assemble(repos, labels, history):
    """Join repos + labels + history into (index items, per-repo details). Pure; easy to test."""
    series, first_seen = history_index(history)
    last, prev = (history[-1] if history else None), (history[-2] if len(history) > 1 else None)
    items, details = [], {}
    for r in repos:
        rid = str(r['id'])
        lab = labels.get(rid) or dict(rule_classify(r), source='rules')
        if not lab.get('in_scope'):
            continue
        gain = None
        if last and prev and rid in last['stars'] and rid in prev['stars']:
            gain = last['stars'][rid] - prev['stars'][rid]
        fs = first_seen.get(rid)
        items.append({'id': r['id'], 'name': r['full_name'], 'slug': slug_of(r['full_name']),
                      'url': safe_url(r.get('html_url')) or f"https://github.com/{r['full_name']}",
                      'summary': lab.get('summary') or r.get('description') or '',
                      'category': lab['category'] if lab.get('category') in CATEGORY_IDS else 'other',
                      'tags': list(lab.get('tags') or [])[:3], 'topics': [t for t in (r.get('topics') or []) if isinstance(t, str)][:20],
                      'stars': int(r['stars']), 'gain': gain, 'forks': int(r.get('forks') or 0),
                      'language': r.get('language'), 'license': r.get('license'), 'created': (r.get('created_at') or '')[:10],
                      'pushed': (r.get('pushed_at') or '')[:10], 'new': bool(prev and fs == last['date']),
                      'src': lab.get('source') or 'rules'})
        raw = r.get('readme') or ''
        details[r['id']] = {'description': r.get('description') or '', 'homepage': safe_url(r.get('homepage')), 'topics': r.get('topics') or [],
                            'readme': clean_readme(raw), 'readme_truncated': len(raw.encode('utf-8')) >= 3000,
                            'history': series.get(rid, []), 'first_seen': fs, 'label_model': lab.get('model'), 'similar': []}
    items.sort(key=lambda x: (-x['stars'], x['name'].lower()))
    for rid, sim in similar_repos(items).items():
        details[rid]['similar'] = sim
    return items, details


def build(out, data_dir=DATA, page=os.path.join(ROOT, 'site', 'index.html'), base=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    base = (base or os.environ.get('SS_BASE_URL') or './').rstrip('/') + '/'
    repos = read_jsonl(os.path.join(data_dir, 'repos.jsonl'))
    if not repos:
        raise ValueError('no data/repos.jsonl; run the pipeline first')
    labels = read_json(os.path.join(data_dir, 'labels', 'labels.json'), {}) or {}
    hist_path = os.path.join(data_dir, 'history', 'stars.jsonl')
    history = sorted(read_jsonl(hist_path), key=lambda h: h['date'])
    last, prev = (history[-1] if history else None), (history[-2] if len(history) > 1 else None)
    items, details = assemble(repos, labels, history)

    tmp = out.rstrip('/') + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        os.makedirs(os.path.join(tmp, 'data', 'r')); os.makedirs(os.path.join(tmp, 'download'))

        def dump(rel, obj):
            with open(os.path.join(tmp, rel), 'w', encoding='utf-8') as f:
                json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))

        gen = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        dump('data/index.json', {'schema': SCHEMA_VERSION, 'generated_at': gen, 'snapshot': last and last['date'],
                                 'prev_snapshot': prev and prev['date'], 'repos': items})
        for it in items:
            dump(f"data/r/{it['slug']}.json", dict(it, **details[it['id']]))
        counts = {c: 0 for c in CATEGORY_IDS}
        srcs = {}
        for it in items:
            counts[it['category']] += 1
            srcs[it['src']] = srcs.get(it['src'], 0) + 1
        cats = [{'id': cid, 'label': label, 'description': d, 'count': counts[cid],
                 'stars': sum(i['stars'] for i in items if i['category'] == cid)} for cid, label, d in CATEGORIES]
        dump('data/categories.json', cats)
        dump('data/stats.json', {'schema': SCHEMA_VERSION, 'generated_at': gen, 'repos': len(items), 'candidates': len(repos),
                                 'out_of_scope': len(repos) - len(items), 'total_stars': sum(i['stars'] for i in items),
                                 'min_stars': MIN_STARS, 'snapshots': len(history), 'first_snapshot': history[0]['date'] if history else None,
                                 'last_snapshot': last and last['date'], 'prev_snapshot': prev and prev['date'],
                                 'new_this_week': sum(1 for i in items if i['new']), 'by_category': counts, 'label_sources': srcs})
        # feed: repos first seen after the baseline snapshot, newest first
        fresh = [dict(it, first_seen=details[it['id']]['first_seen']) for it in items
                 if history and (details[it['id']]['first_seen'] or '') > history[0]['date']]
        fresh.sort(key=lambda x: (x['first_seen'], x['stars']), reverse=True)
        with open(os.path.join(tmp, 'feed.xml'), 'w', encoding='utf-8') as f:
            f.write(rss(fresh[:50], base, now))
        with open(os.path.join(tmp, 'download', 'skillstars-data.zip'), 'wb') as f:
            f.write(data_zip(items, details, cats, hist_path, now))
        shutil.copyfile(page, os.path.join(tmp, 'index.html'))      # content only, not the source file's mode
        open(os.path.join(tmp, '.nojekyll'), 'w').close()
        for d, dirs, files in os.walk(tmp):                          # servable regardless of the local umask
            os.chmod(d, 0o755)
            for f in files:
                os.chmod(os.path.join(d, f), 0o644)

        validate(tmp)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)     # never leave a half-built site around
        raise
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.replace(tmp, out)
    return {'repos': len(items), 'candidates': len(repos), 'snapshots': len(history), 'by_category': counts}


def _check(cond, msg):
    if not cond:
        raise ValueError(msg)


def _types(obj, spec, where):
    for k, t in spec.items():
        _check(k in obj, f'{where}: missing {k}')
        _check(isinstance(obj[k], t) and not (t is int and isinstance(obj[k], bool)), f'{where}: {k} has type {type(obj[k]).__name__}')


def validate(out):
    """Schema check of a built site. Raises ValueError on the first problem; used by build() and the tests."""
    with open(os.path.join(out, 'data', 'index.json'), encoding='utf-8') as f:
        idx = json.load(f)
    _check(idx.get('schema') == SCHEMA_VERSION and isinstance(idx.get('repos'), list), 'index.json: bad header')
    repos = idx['repos']
    _check(len(repos) > 0, 'index.json: no repos')
    ids = set()
    for r in repos:
        _types(r, INDEX_FIELDS, f"index.json {r.get('name')}")
        _check(r['category'] in CATEGORY_IDS, f"{r['name']}: unknown category {r['category']}")
        _check(len(r['tags']) <= 3 and all(isinstance(t, str) for t in r['tags']), f"{r['name']}: bad tags")
        _check(all(isinstance(t, str) for t in r['topics']), f"{r['name']}: bad topics")
        _check(r['url'].startswith(('https://', 'http://')), f"{r['name']}: unsafe url")
        _check(r['stars'] >= 0 and r['id'] not in ids, f"{r['name']}: bad stars or duplicate id")
        ids.add(r['id'])
        p = os.path.join(out, 'data', 'r', r['slug'] + '.json')
        _check(os.path.exists(p), f'missing {p}')
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        _types(d, DETAIL_FIELDS, f'r/{r["slug"]}.json')
        _check(all(isinstance(h, list) and len(h) == 2 for h in d['history']), f"{r['slug']}: bad history")
        _check(d['homepage'] is None or d['homepage'].startswith(('https://', 'http://')), f"{r['slug']}: unsafe homepage {d['homepage']!r}")
    with open(os.path.join(out, 'data', 'categories.json'), encoding='utf-8') as f:
        cats = json.load(f)
    _check([c['id'] for c in cats] == CATEGORY_IDS, 'categories.json: ids differ from taxonomy')
    _check(sum(c['count'] for c in cats) == len(repos), 'categories.json: counts do not add up')
    with open(os.path.join(out, 'data', 'stats.json'), encoding='utf-8') as f:
        stats = json.load(f)
    for k in ('generated_at', 'repos', 'candidates', 'snapshots', 'last_snapshot', 'by_category', 'label_sources', 'new_this_week'):
        _check(k in stats, f'stats.json: missing {k}')
    _check(stats['repos'] == len(repos), 'stats.json: repo count mismatch')
    import xml.etree.ElementTree as ET
    ET.parse(os.path.join(out, 'feed.xml'))
    with zipfile.ZipFile(os.path.join(out, 'download', 'skillstars-data.zip')) as z:
        _check(ZIP_MEMBERS <= set(z.namelist()), f'zip: missing {ZIP_MEMBERS - set(z.namelist())}')
        rows = list(csv.DictReader(io.StringIO(z.read('skillstars.csv').decode('utf-8'))))
        _check(len(rows) == len(repos) and list(rows[0]) == CSV_COLUMNS, 'zip: csv rows/columns')
    _check(os.path.exists(os.path.join(out, 'index.html')), 'index.html missing')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--data', default=DATA)
    a = ap.parse_args(argv)
    t = time.time()
    try:
        res = build(a.out, a.data)
    except (ValueError, OSError, KeyError) as e:
        log(f'BUILD FAILED, previous site kept: {e!r}')
        sys.exit(1)
    log(f"site_dist: {res['repos']} repos ({res['candidates']} candidates), {res['snapshots']} snapshots, "
        f"{time.time() - t:.1f}s -> {os.path.relpath(a.out)}")
    for cid in CATEGORY_IDS:
        log(f'  {LABELS[cid]:30} {res["by_category"][cid]}')


if __name__ == '__main__':
    main()
