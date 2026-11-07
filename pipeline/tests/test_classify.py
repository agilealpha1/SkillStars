import json, os, tempfile, unittest
from helpers import repo
import classify, taxonomy


def read(path):
    with open(path) as f:
        return json.load(f)


class TestRuleFallback(unittest.TestCase):
    def cat(self, r):
        return taxonomy.rule_classify(r)['category']

    def test_categories(self):
        self.assertEqual(self.cat(repo('o/awesome-mcp-servers', description='A collection of MCP servers.')), 'awesome')
        self.assertEqual(self.cat(repo('o/servers', description='Model Context Protocol Servers')), 'mcp')
        self.assertEqual(self.cat(repo('o/skills', description='Public repository for Agent Skills')), 'skills')
        self.assertEqual(self.cat(repo('o/rules', description='Configuration files that enhance Cursor AI editor. .cursorrules')), 'coding')
        self.assertEqual(self.cat(repo('o/guide', description='Guides, papers and resources for prompt engineering')), 'prompts')
        self.assertEqual(self.cat(repo('o/mem', description='Universal memory layer for AI Agents')), 'rag-memory')
        self.assertEqual(self.cat(repo('o/bu', description='Make websites accessible for AI agents. Browser automation.')), 'browser')
        self.assertEqual(self.cat(repo('o/lf', description='LLM observability, evals and prompt management')), 'evals')
        self.assertEqual(self.cat(repo('o/lc', description='Build context-aware agent framework apps', topics=['langchain'])), 'frameworks')

    def test_agent_builders_and_terminal_agents(self):
        self.assertEqual(self.cat(repo('o/langflow', description='A tool for building and deploying AI-powered agents and workflows.', topics=['mcp'])), 'frameworks')
        self.assertEqual(self.cat(repo('o/gemini-cli', description='An open-source AI agent that brings Gemini directly into your terminal.', topics=['mcp-server'])), 'coding')

    def test_description_outweighs_readme_and_topics(self):
        r = repo('o/x', description='Model Context Protocol server for Postgres', topics=['rag', 'vector-database'],
                 readme='rag rag rag rag rag retrieval-augmented embeddings')
        self.assertEqual(self.cat(r), 'mcp')

    def test_awesome_from_name_or_description_only(self):
        self.assertNotEqual(self.cat(repo('o/tips', description='CSS tips for agents', topics=['awesome-list'])), 'awesome')
        self.assertEqual(self.cat(repo('o/list', description='A curated list of AI agent tools')), 'awesome')

    def test_nothing_matches_is_other(self):
        self.assertEqual(self.cat(repo('o/x', description='Something with LLMs')), 'other')

    def test_tags_and_summary(self):
        out = taxonomy.rule_classify(repo('o/x', description='Claude MCP server for browser automation ' + 'x' * 200))
        self.assertLessEqual(len(out['tags']), 3)
        self.assertNotIn(out['category'], out['tags'])
        self.assertLessEqual(len(out['summary']), 141)


class TestScopeGate(unittest.TestCase):
    def test_in_scope(self):
        self.assertTrue(taxonomy.in_scope(repo('o/servers', description='Model Context Protocol Servers')))
        self.assertTrue(taxonomy.in_scope(repo('o/hermes-agent', description='The agent that grows with you')))
        self.assertTrue(taxonomy.in_scope(repo('o/thing', description='nothing relevant', via=['seed'])))

    def test_out_of_scope(self):
        no = lambda **kw: self.assertFalse(taxonomy.in_scope(repo(**kw)), kw)
        no(full_name='o/starship', description='The minimal, blazing-fast prompt for any shell!', via=['name:prompts'])
        no(full_name='o/css-protips', description='Tips to take your CSS skills pro', topics=['agent-skills'], via=['name:skills'])
        no(full_name='o/ansible', description='Radically simple IT automation, no agents to install', via=['name:agent'])
        no(full_name='o/ollama', description='Get up and running with large language models.', via=['readme:Claude Code'])
        no(full_name='o/factory', description='Unified efficient fine-tuning of 100+ LLMs', topics=['agent'])
        no(full_name='o/v2ray-agent', description='Xray/v2ray install script', via=['name:agent'])
        no(full_name='o/spaceship-prompt', description='Minimalistic, powerful and extremely customizable Zsh prompt', topics=['prompt'], via=['name:prompts'])
        no(full_name='o/ua-parser-js', description='Detect browsers, OS, devices, bots and AI crawlers from User-Agent data', via=['name:agent'])
        no(full_name='o/JavaGuide', description='Java interview guide covering databases, system design and AI app development', topics=['agent', 'mcp'], via=['topic:mcp'])
        no(full_name='o/pest', description='The elegant testing framework for PHP developers and AI agents.', via=['name:agent'])

    def test_specific_topic_needs_some_ai_signal(self):
        self.assertFalse(taxonomy.in_scope(repo('o/checklist', description='Front-end checklist', topics=['mcp-server'], via=['topic:mcp-server'])))
        self.assertTrue(taxonomy.in_scope(repo('o/pg', description='Postgres tools for AI', topics=['mcp-server'], via=['topic:mcp-server'])))


class TestCleanLabel(unittest.TestCase):
    def test_malformed_llm_answer_falls_back_field_by_field(self):
        r = repo('o/servers', description='Model Context Protocol Servers')
        out = classify.clean_label({'category': 'nonsense', 'tags': ['Claude Code!', 'MCP', 'mcp', 'a', 'b'], 'summary': '', 'in_scope': 'yes'}, r)
        self.assertEqual(out['category'], 'mcp')
        self.assertEqual(out['tags'], ['claude-code', 'a', 'b'])          # 'mcp' equals the category, dupes dropped
        self.assertEqual(out['summary'], 'Model Context Protocol Servers')
        self.assertIs(out['in_scope'], True)

    def test_parse_answer(self):
        self.assertEqual(classify.parse_answer('Sure:\n{"category": "mcp"}\n'), {'category': 'mcp'})
        self.assertIsNone(classify.parse_answer('no json here'))

    def test_state_stale(self):
        now = 1_800_000_000
        self.assertTrue(classify.state_stale({}, now))
        self.assertTrue(classify.state_stale({'batch_id': 'b', 'submitted_at': now - 30 * 86400}, now))
        self.assertTrue(classify.state_stale({'batch_id': 'b', 'submitted_at': now, 'errors': 3}, now))
        self.assertFalse(classify.state_stale({'batch_id': 'b', 'submitted_at': now - 86400}, now))


class TestClassifyRun(unittest.TestCase):
    """End-to-end without a key: rule labels for everyone, cached LLM labels survive."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_key = os.environ.pop('ANTHROPIC_API_KEY', None)
        self.repos = [repo('o/servers', rid=1, description='Model Context Protocol Servers', readme_hash='new'),
                      repo('o/skills', rid=2, description='Public repository for Agent Skills')]
        self.paths = {k: os.path.join(self.tmp, k) for k in ('repos.jsonl', 'labels.json', 'state.json')}
        with open(self.paths['repos.jsonl'], 'w') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in self.repos))

    def tearDown(self):
        if self.old_key is not None:
            os.environ['ANTHROPIC_API_KEY'] = self.old_key

    def run_classify(self):
        return classify.main(['--repos', self.paths['repos.jsonl'], '--labels', self.paths['labels.json'], '--state', self.paths['state.json']])

    def test_no_key_uses_rules_and_keeps_llm_labels(self):
        llm = {'category': 'other', 'tags': ['x'], 'summary': 'LLM summary', 'in_scope': True, 'key': '1:old:v1',
               'source': 'llm', 'model': 'claude-haiku-4-5', 'labeled_at': '2026-10-01T00:00:00Z'}
        gone = dict(llm, key='99:h:v1', last_seen='2026-10-30')      # repo absent this run, seen recently
        ancient = dict(llm, key='98:h:v1', last_seen='2020-01-01')   # absent for years
        with open(self.paths['labels.json'], 'w') as f:
            json.dump({'1': llm, '99': gone, '98': ancient}, f)
        labels = self.run_classify()
        self.assertEqual(labels['1']['source'], 'llm')               # README changed, but no key: stale LLM label kept
        self.assertEqual(labels['1']['summary'], 'LLM summary')
        self.assertEqual(labels['2']['source'], 'rules')
        self.assertEqual(labels['2']['category'], 'skills')
        self.assertIn('99', labels)                                   # retained across runs
        self.assertNotIn('98', labels)                                # pruned after --prune-days
        on_disk = read(self.paths['labels.json'])
        self.assertEqual(set(on_disk), {'1', '2', '99'})
        for v in on_disk.values():
            self.assertIn(v['category'], taxonomy.CATEGORY_IDS)



class FakeResp:
    def __init__(self, obj=None, text=''):
        self._obj, self.text = obj, text

    def json(self):
        return self._obj


class TestBatchFlow(unittest.TestCase):
    """The Haiku path with the Anthropic API mocked out: submit, poll, collect; and stale-batch recovery."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_key, self.old_call = os.environ.get('ANTHROPIC_API_KEY'), classify.call
        os.environ['ANTHROPIC_API_KEY'] = 'test-key'
        self.repos = [repo('o/servers', rid=1, description='Model Context Protocol Servers'),
                      repo('o/skills', rid=2, description='Public repository for Agent Skills')]
        self.p = {k: os.path.join(self.tmp, k) for k in ('repos.jsonl', 'labels.json', 'state.json')}
        with open(self.p['repos.jsonl'], 'w') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in self.repos))
        self.requests = []

    def tearDown(self):
        classify.call = self.old_call
        if self.old_key is None:
            os.environ.pop('ANTHROPIC_API_KEY', None)
        else:
            os.environ['ANTHROPIC_API_KEY'] = self.old_key

    def fake_api(self, gone=()):
        answers = {'1': '{"in_scope": true, "category": "mcp", "tags": ["reference"], "summary": "Reference MCP servers."}',
                   '2': 'not json at all'}

        def call(method, url, key, body=None, **kw):
            self.requests.append((method, url))
            if any(g in url for g in gone):
                raise classify.BatchGone(url)
            if method == 'POST':
                self.submitted = [r['custom_id'] for r in body['requests']]
                return FakeResp({'id': 'batch_new'})
            if url.endswith('/results'):
                lines = [{'custom_id': cid, 'result': {'type': 'succeeded', 'message': {'content': [{'type': 'text', 'text': answers[cid[2:]]}]}}}
                         for cid in self.submitted]
                return FakeResp(text='\n'.join(json.dumps(l) for l in lines))
            return FakeResp({'processing_status': 'ended', 'results_url': url + '/results', 'request_counts': {}})
        return call

    def run_classify(self):
        return classify.main(['--repos', self.p['repos.jsonl'], '--labels', self.p['labels.json'], '--state', self.p['state.json']])

    def test_submit_collect_and_cache(self):
        classify.call = self.fake_api()
        labels = self.run_classify()
        self.assertEqual(sorted(self.submitted), ['r-1', 'r-2'])
        self.assertEqual(labels['1']['source'], 'llm')
        self.assertEqual(labels['1']['summary'], 'Reference MCP servers.')
        self.assertEqual(labels['2']['source'], 'rules')               # unparseable answer -> rules, retried next run
        self.assertFalse(os.path.exists(self.p['state.json']))
        self.requests.clear()
        labels = self.run_classify()                                    # second run: only the failed repo is resent
        self.assertEqual(self.submitted, ['r-2'])

    def test_gone_batch_is_dropped_and_resubmitted(self):
        with open(self.p['state.json'], 'w') as f:
            json.dump({'batch_id': 'batch_old', 'submitted_at': __import__('time').time(), 'keys': {}, 'model': 'm'}, f)
        classify.call = self.fake_api(gone=('batch_old',))
        labels = self.run_classify()
        self.assertIn(('POST', classify.API), self.requests)
        self.assertEqual(labels['1']['source'], 'llm')
        self.assertFalse(os.path.exists(self.p['state.json']))

    def test_unreadable_batch_counts_errors_then_gives_up(self):
        state = {'batch_id': 'batch_old', 'submitted_at': __import__('time').time(), 'keys': {}, 'model': 'm', 'errors': 0}
        with open(self.p['state.json'], 'w') as f:
            json.dump(state, f)
        def flaky(method, url, key, body=None, **kw):
            raise RuntimeError('network down')
        classify.call = flaky
        for expected in (1, 2, 3):
            self.run_classify()
            self.assertEqual(read(self.p['state.json'])['errors'], expected)
        classify.call = self.fake_api()
        labels = self.run_classify()                                    # 3 errors -> stale -> dropped, resubmitted
        self.assertEqual(labels['1']['source'], 'llm')

    def test_one_deadline_for_pending_and_new_batch(self):
        """A still-running batch uses up the budget; no second batch is submitted and its state is kept."""
        import time as _t
        with open(self.p['state.json'], 'w') as f:
            json.dump({'batch_id': 'batch_old', 'submitted_at': _t.time(), 'keys': {}, 'model': 'm'}, f)
        def running(method, url, key, body=None, **kw):
            self.requests.append((method, url))
            return FakeResp({'processing_status': 'in_progress', 'request_counts': {'processing': 2}})
        classify.call = running
        t0 = _t.time()
        classify.main(['--repos', self.p['repos.jsonl'], '--labels', self.p['labels.json'], '--state', self.p['state.json'], '--max-wait', '0'])
        self.assertLess(_t.time() - t0, 5)
        self.assertNotIn('POST', [m for m, _ in self.requests])
        self.assertEqual(read(self.p['state.json'])['batch_id'], 'batch_old')

    def test_new_batch_that_outlives_the_budget_is_saved_for_next_run(self):
        def api(method, url, key, body=None, **kw):
            if method == 'POST':
                return FakeResp({'id': 'batch_new'})
            return FakeResp({'processing_status': 'in_progress', 'request_counts': {}})
        classify.call = api
        labels = classify.main(['--repos', self.p['repos.jsonl'], '--labels', self.p['labels.json'], '--state', self.p['state.json'], '--max-wait', '0'])
        state = read(self.p['state.json'])
        self.assertEqual(state['batch_id'], 'batch_new')
        self.assertEqual(set(state['keys']), {'1', '2'})
        self.assertTrue(all(v['source'] == 'rules' for v in labels.values()))   # meanwhile: rule labels

    def test_call_retries_on_overload(self):
        import requests
        seq = iter([529, 429, 200])
        class R:
            def __init__(self, code): self.status_code, self.headers, self.text = code, {}, ''
            def raise_for_status(self): pass
        orig = requests.request
        requests.request = lambda *a, **k: R(next(seq))
        try:
            r = self.old_call('GET', classify.API, 'k', sleep=lambda s: None)
        finally:
            requests.request = orig
        self.assertEqual(r.status_code, 200)


if __name__ == '__main__':
    unittest.main()
