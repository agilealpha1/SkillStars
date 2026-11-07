"""Fixtures shared by the tests: tiny repo dicts in the shape discover.normalize()/enrich.py produce."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def repo(full_name, rid=None, stars=20000, description='', topics=(), readme='', via=('topic:mcp',), **kw):
    r = {'id': rid if rid is not None else abs(hash(full_name)) % 10 ** 9, 'full_name': full_name,
         'html_url': f'https://github.com/{full_name}', 'description': description, 'stars': stars, 'forks': 100,
         'language': 'Python', 'license': 'MIT', 'topics': list(topics), 'created_at': '2024-01-01T00:00:00Z',
         'pushed_at': '2026-11-01T00:00:00Z', 'homepage': None, 'default_branch': 'main', 'via': list(via),
         'readme': readme, 'readme_hash': 'h0'}
    r.update(kw)
    return r


def search_item(full_name, rid, stars=20000, archived=False, fork=False, **kw):
    it = {'id': rid, 'full_name': full_name, 'html_url': f'https://github.com/{full_name}', 'description': 'd',
          'stargazers_count': stars, 'forks_count': 1, 'language': 'Python', 'license': {'spdx_id': 'MIT', 'name': 'MIT'},
          'topics': ['mcp'], 'created_at': '2024-01-01T00:00:00Z', 'pushed_at': '2026-11-01T00:00:00Z',
          'homepage': '', 'archived': archived, 'fork': fork, 'default_branch': 'main'}
    it.update(kw)
    return it
