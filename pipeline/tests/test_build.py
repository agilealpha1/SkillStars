import csv, datetime as dt, io, json, os, shutil, tempfile, unittest, zipfile
import xml.etree.ElementTree as ET
from helpers import repo
import build_site_data as B, snapshot, taxonomy


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(''.join(json.dumps(r) + '\n' for r in rows))


class TestBuildOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data, self.out = os.path.join(self.tmp, 'data'), os.path.join(self.tmp, 'site_dist')
        self.page = os.path.join(self.tmp, 'index.html')
        with open(self.page, 'w') as f:
            f.write('<!doctype html><title>SkillStars</title>')
        self.repos = [
            repo('anthropics/skills', rid=1, stars=12000, description='Public repository for Agent Skills', topics=['agent-skills'],
                 readme='# Skills\n[![badge](https://x/y.svg)](https://x)\n<p align="center"><img src="a.png"></p>\nSee [docs](https://d).'),
            repo('o/servers', rid=2, stars=30000, description='Model Context Protocol Servers', homepage='javascript:alert(1)',
                 topics=['mcp', 'sdlc']),
            repo('o/starship', rid=3, stars=50000, description='The prompt for any shell', via=['name:prompts']),
            repo('o/brand-new', rid=4, stars=10100, description='MCP server for Postgres', homepage='flashml.ai'),
        ]
        write_jsonl(os.path.join(self.data, 'repos.jsonl'), self.repos)
        os.makedirs(os.path.join(self.data, 'labels'))
        with open(os.path.join(self.data, 'labels', 'labels.json'), 'w') as f:
            json.dump({'1': {'category': 'skills', 'tags': ['claude', 'skills-pack'], 'summary': 'Official Agent Skills from Anthropic.',
                         'in_scope': True, 'source': 'llm', 'model': 'claude-haiku-4-5'},
                       '3': {'category': 'prompts', 'tags': [], 'summary': 'x', 'in_scope': False, 'source': 'rules'}},
                      f)     # 2 and 4 get rule labels on the fly
        h = []
        h = snapshot.add_snapshot(h, [dict(r, stars=r['stars'] - 500) for r in self.repos[:3]], dt.date(2026, 10, 31))
        h = snapshot.add_snapshot(h, self.repos, dt.date(2026, 11, 7))
        write_jsonl(os.path.join(self.data, 'history', 'stars.jsonl'), h)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        return B.build(self.out, self.data, page=self.page, base='https://example.github.io/SkillStars',
                       now=dt.datetime(2026, 11, 9, 3, 30, tzinfo=dt.timezone.utc))

    def load(self, rel):
        with open(os.path.join(self.out, rel), encoding='utf-8') as f:
            return json.load(f)

    def test_schema_and_contents(self):
        res = self.build()
        self.assertTrue(B.validate(self.out))
        idx = self.load('data/index.json')
        self.assertEqual(idx['snapshot'], '2026-11-07')
        self.assertEqual(idx['prev_snapshot'], '2026-10-31')
        by = {r['name']: r for r in idx['repos']}
        self.assertEqual(set(by), {'anthropics/skills', 'o/servers', 'o/brand-new'})   # out-of-scope starship excluded
        self.assertEqual([r['name'] for r in idx['repos']], ['o/servers', 'anthropics/skills', 'o/brand-new'])  # by stars
        for r in idx['repos']:
            self.assertEqual(set(r), set(B.INDEX_FIELDS))
        self.assertEqual(by['anthropics/skills']['gain'], 500)
        self.assertEqual(by['anthropics/skills']['src'], 'llm')
        self.assertFalse(by['anthropics/skills']['new'])
        self.assertIsNone(by['o/brand-new']['gain'])
        self.assertTrue(by['o/brand-new']['new'])
        self.assertEqual(by['o/servers']['category'], 'mcp')
        self.assertEqual(res['repos'], 3)

    def test_per_repo_file(self):
        self.build()
        d = self.load('data/r/anthropics__skills.json')
        self.assertTrue(set(B.DETAIL_FIELDS) <= set(d))
        self.assertEqual(d['history'], [['2026-10-31', 11500], ['2026-11-07', 12000]])
        self.assertNotIn('badge', d['readme']); self.assertNotIn('<img', d['readme']); self.assertIn('See docs.', d['readme'])
        self.assertTrue(all(os.path.exists(os.path.join(self.out, 'data', 'r', s + '.json')) for s in d['similar']))

    def test_output_is_world_readable(self):
        os.chmod(self.page, 0o600)                                   # source mode must not leak into the site
        self.build()
        for d, _, files in os.walk(self.out):
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o755, d)
            for f in files:
                self.assertEqual(os.stat(os.path.join(d, f)).st_mode & 0o777, 0o644, f)

    def test_urls_are_sanitized(self):
        self.build()
        self.assertIsNone(self.load('data/r/o__servers.json')['homepage'])             # javascript: dropped
        self.assertEqual(self.load('data/r/o__brand-new.json')['homepage'], 'https://flashml.ai')   # bare domain fixed
        for r in self.load('data/index.json')['repos']:
            self.assertTrue(r['url'].startswith('https://'))

    def test_every_topic_pill_finds_its_own_repo(self):
        """Topic pills link to #/?q=<topic>; the site searches index.json, so topics must be in it."""
        self.build()
        idx = {r['slug']: r for r in self.load('data/index.json')['repos']}
        self.assertEqual(idx['o__servers']['topics'], ['mcp', 'sdlc'])
        for slug, r in idx.items():
            for t in self.load(f'data/r/{slug}.json')['topics']:
                self.assertIn(t, r['topics'])

    def test_categories_stats_feed_zip(self):
        self.build()
        cats = self.load('data/categories.json')
        self.assertEqual([c['id'] for c in cats], taxonomy.CATEGORY_IDS)
        self.assertEqual(sum(c['count'] for c in cats), 3)
        stats = self.load('data/stats.json')
        self.assertEqual((stats['repos'], stats['candidates'], stats['snapshots'], stats['new_this_week']), (3, 4, 2, 1))
        self.assertEqual(stats['label_sources'], {'llm': 1, 'rules': 2})
        items = ET.parse(os.path.join(self.out, 'feed.xml')).getroot().findall('./channel/item')
        self.assertEqual([i.findtext('title') for i in items], ['o/brand-new (10,100 stars)'])
        self.assertTrue(items[0].findtext('link').startswith('https://example.github.io/SkillStars/#/r/'))
        with zipfile.ZipFile(os.path.join(self.out, 'download', 'skillstars-data.zip')) as z:
            self.assertTrue(B.ZIP_MEMBERS <= set(z.namelist()))
            rows = list(csv.DictReader(io.StringIO(z.read('skillstars.csv').decode())))
            self.assertEqual(len(rows), 3)
            self.assertIn('CC0', z.read('LICENSE.txt').decode())
            self.assertEqual(len(json.loads(z.read('skillstars.json'))), 3)
        self.assertTrue(os.path.exists(os.path.join(self.out, 'index.html')))
        self.assertTrue(os.path.exists(os.path.join(self.out, '.nojekyll')))

    def test_failed_build_keeps_previous_site(self):
        self.build()
        before = self.load('data/index.json')
        os.remove(self.page)                                       # next build fails before the swap
        with self.assertRaises(OSError):
            self.build()
        self.assertEqual(self.load('data/index.json'), before)
        self.assertFalse(os.path.exists(self.out + '.tmp'))

    def test_validate_rejects_broken_output(self):
        self.build()
        idx = self.load('data/index.json')
        idx['repos'][0]['category'] = 'nope'
        with open(os.path.join(self.out, 'data', 'index.json'), 'w') as f:
            json.dump(idx, f)
        with self.assertRaises(ValueError):
            B.validate(self.out)


class TestSnapshot(unittest.TestCase):
    def test_same_week_replaces_and_new_week_appends(self):
        r = [repo('a/b', rid=1, stars=10)]
        h = snapshot.add_snapshot([], r, dt.date(2026, 11, 2))          # Monday, W45
        h = snapshot.add_snapshot(h, [dict(r[0], stars=11)], dt.date(2026, 11, 8))   # Sunday, still W45
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]['stars'], {'1': 11})
        h = snapshot.add_snapshot(h, [dict(r[0], stars=15)], dt.date(2026, 11, 9))   # W46
        self.assertEqual([x['week'] for x in h], ['2026-W45', '2026-W46'])


class TestCleanReadme(unittest.TestCase):
    def test_code_fences_are_verbatim(self):
        t = B.clean_readme('<p>Use <b>it</b></p>\n```tsx\n<Component prop={1} />\nconst m: Map<K, V> = new Map()\n---\n```\nSee `__init__.py`')
        self.assertIn('<Component prop={1} />', t)
        self.assertIn('Map<K, V>', t)
        self.assertIn('---', t)
        self.assertIn('`__init__.py`', t)
        self.assertTrue(t.startswith('Use it'))

    def test_unclosed_fence_is_code(self):
        self.assertTrue(B.clean_readme('intro\n~~~\n<div>kept</div>').endswith('<div>kept</div>'))

    def test_strips_html_badges_and_keeps_text(self):
        t = B.clean_readme('<!-- c -->\n<h1 align="center">Title</h1>\n\n\n\n![img](a.png)\n- item [link](u)\n```\ncode\n```\n---\n')
        self.assertEqual(t, 'Title\n\n- item link\n```\ncode\n```')


if __name__ == '__main__':
    unittest.main()
