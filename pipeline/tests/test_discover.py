import os, tempfile, unittest
from helpers import search_item
import discover


class TestDedupe(unittest.TestCase):
    def test_same_repo_from_two_queries_is_one_entry_with_both_vias(self):
        found = {}
        discover.merge(found, discover.normalize(search_item('a/mcp', 1)), 'topic:mcp')
        discover.merge(found, discover.normalize(search_item('a/mcp', 1)), 'name:mcp')
        discover.merge(found, discover.normalize(search_item('a/mcp', 1)), 'name:mcp')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[1]['via'], ['topic:mcp', 'name:mcp'])

    def test_dedupe_is_by_id_so_renames_collapse_and_take_the_newest_name(self):
        found = {}
        discover.merge(found, discover.normalize(search_item('old/name', 7, stars=10500)), 'topic:mcp')
        discover.merge(found, discover.normalize(search_item('new/name', 7, stars=10600)), 'seed')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[7]['full_name'], 'new/name')
        self.assertEqual(found[7]['stars'], 10600)
        self.assertEqual(found[7]['via'], ['topic:mcp', 'seed'])

    def test_different_ids_same_name_stay_separate(self):
        found = {}
        discover.merge(found, discover.normalize(search_item('x/y', 1)), 'a')
        discover.merge(found, discover.normalize(search_item('x/y', 2)), 'b')
        self.assertEqual(len(found), 2)


class TestInclusionRule(unittest.TestCase):
    def test_keep(self):
        n = discover.normalize
        self.assertTrue(discover.keep(n(search_item('a/b', 1, stars=10000))))
        self.assertFalse(discover.keep(n(search_item('a/b', 1, stars=9999))))
        self.assertFalse(discover.keep(n(search_item('a/b', 1, archived=True))))
        self.assertFalse(discover.keep(n(search_item('a/b', 1, fork=True))))

    def test_normalize_license_and_nulls(self):
        r = discover.normalize(search_item('a/b', 1, license={'spdx_id': 'NOASSERTION', 'name': 'Other'}, description=None, homepage=''))
        self.assertEqual(r['license'], 'Other')
        self.assertEqual(r['description'], '')
        self.assertIsNone(r['homepage'])
        self.assertIsNone(discover.normalize(search_item('a/b', 1, license=None))['license'])


class FakeSearcher(discover.Searcher):
    """Pretends 2,500 repos have 10,000..12,499 stars (one per star count) to exercise range splitting."""
    def __init__(self):
        self.calls = []

    def page(self, q, page):
        import re
        self.calls.append((q, page))
        m = re.search(r'stars:(?:>=(\d+)|(\d+)\.\.(\d+))', q)
        lo = int(m.group(1) or m.group(2)); hi = int(m.group(3)) if m.group(3) else 12499
        ids = list(range(max(lo, 10000), min(hi, 12499) + 1))
        chunk = ids[(page - 1) * 100: page * 100]
        return {'total_count': len(ids), 'items': [search_item(f'o/r{i}', i, stars=i) for i in chunk]}


class TestStarRangeSplit(unittest.TestCase):
    def test_every_slice_fits_under_the_1000_cap_and_nothing_is_lost(self):
        s = FakeSearcher()
        items = s.search('topic:x')
        self.assertEqual(len({it['id'] for it in items}), 2500)
        self.assertTrue(all(p <= 10 for _, p in s.calls))
        fetched = [q for q, p in s.calls if p > 1]
        self.assertTrue(fetched, 'expected multi-page slices')


class TestSafeUrl(unittest.TestCase):
    def test_only_http_urls_survive_and_bare_domains_get_https(self):
        from common import safe_url
        self.assertEqual(safe_url('flashml.ai'), 'https://flashml.ai')
        self.assertEqual(safe_url('www.example.com/docs#x'), 'https://www.example.com/docs#x')
        self.assertEqual(safe_url(' https://ok.io/a?b=1 '), 'https://ok.io/a?b=1')
        self.assertEqual(safe_url('http://localhost:3000'), 'http://localhost:3000')
        self.assertEqual(safe_url('example.com:8080/docs'), 'https://example.com:8080/docs')   # host:port, not a scheme
        for bad in ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', ' javascript:alert(1)', 'data:text/html,<b>x', 'vbscript:x',
                    'mailto:a@b.c', 'ftp://x.org', 'localhost:3000', 'https://', 'https://a.io/"><script>', 'not a url', '', None]:
            self.assertIsNone(safe_url(bad), bad)

    def test_normalize_sanitizes_homepage(self):
        self.assertEqual(discover.normalize(search_item('a/b', 1, homepage='flashml.ai'))['homepage'], 'https://flashml.ai')
        self.assertIsNone(discover.normalize(search_item('a/b', 1, homepage='javascript:alert(document.domain)'))['homepage'])


class TestSeeds(unittest.TestCase):
    def test_read_seeds_skips_comments_blank_and_duplicates(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write('# header\n\nanthropics/skills  # comment\nnot-a-repo\nanthropics/skills\nobra/superpowers\n')
        try:
            self.assertEqual(discover.read_seeds(f.name), ['anthropics/skills', 'obra/superpowers'])
        finally:
            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main()
