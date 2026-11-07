"""deploy/update.sh with the pipeline steps replaced by stubs: data/ must stay consistent whatever fails."""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
STUB = r'''
import json, os, sys
step = os.path.basename(sys.argv[0])[:-3]
if step in os.environ.get('STUB_FAIL', '').split(','):
    sys.exit(1)
def w(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(obj, open(p, 'w'))
if step == 'discover': w('data/candidates.json', {'v': 'new'})
if step == 'enrich': w('data/repos.json', {'v': 'new'}); open('data/repos.jsonl', 'w').write('{"v": "new"}\n')
if step == 'classify': w('data/labels/labels.json', {'v': 'new'}); w('data/labels/batch_state.json', {'batch_id': 'b-new'})
if step == 'snapshot': open('data/history/stars.jsonl', 'a').write('{"week": "new"}\n')
if step == 'build_site_data':
    out = sys.argv[sys.argv.index('--out') + 1]; os.makedirs(out, exist_ok=True)
'''


def put(path, text):
    with open(path, 'w') as f:
        f.write(text)


@unittest.skipUnless(shutil.which('bash') and shutil.which('tar'), 'needs bash and tar')
class TestUpdateScript(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, 'deploy')); os.makedirs(os.path.join(self.d, 'pipeline'))
        shutil.copy(os.path.join(ROOT, 'deploy', 'update.sh'), os.path.join(self.d, 'deploy'))
        for s in ('discover', 'enrich', 'classify', 'snapshot', 'build_site_data'):
            put(os.path.join(self.d, 'pipeline', s + '.py'), STUB)
        for rel, text in {'data/candidates.json': '{"v": "old"}', 'data/repos.jsonl': '{"v": "old"}\n',
                          'data/labels/labels.json': '{"v": "old"}', 'data/history/stars.jsonl': '{"week": "old"}\n'}.items():
            os.makedirs(os.path.dirname(os.path.join(self.d, rel)), exist_ok=True)
            put(os.path.join(self.d, rel), text)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_update(self, fail=''):
        env = dict(os.environ, PY=sys.executable, STUB_FAIL=fail)
        return subprocess.run(['bash', 'deploy/update.sh'], cwd=self.d, env=env, capture_output=True, text=True).returncode

    def read(self, rel):
        with open(os.path.join(self.d, rel)) as f:
            return f.read()

    def test_success_updates_everything(self):
        self.assertEqual(self.run_update(), 0)
        self.assertIn('new', self.read('data/candidates.json'))
        self.assertIn('new', self.read('data/repos.jsonl'))
        self.assertIn('new', self.read('data/history/stars.jsonl'))

    def test_enrich_failure_restores_candidates_and_skips_snapshot_but_keeps_labels(self):
        self.assertEqual(self.run_update('enrich'), 0)
        self.assertIn('old', self.read('data/candidates.json'))      # not paired with stale repos.jsonl
        self.assertIn('old', self.read('data/repos.jsonl'))
        self.assertNotIn('new', self.read('data/history/stars.jsonl'))
        self.assertIn('new', self.read('data/labels/labels.json'))
        self.assertIn('b-new', self.read('data/labels/batch_state.json'))

    def test_discover_failure_keeps_old_data(self):
        self.assertEqual(self.run_update('discover'), 0)
        self.assertIn('old', self.read('data/candidates.json'))
        self.assertNotIn('new', self.read('data/history/stars.jsonl'))

    def test_build_failure_restores_data_but_keeps_paid_labels(self):
        self.assertEqual(self.run_update('build_site_data'), 1)
        self.assertIn('old', self.read('data/candidates.json'))
        self.assertIn('old', self.read('data/repos.jsonl'))
        self.assertNotIn('new', self.read('data/history/stars.jsonl'))
        self.assertIn('new', self.read('data/labels/labels.json'))
        self.assertIn('b-new', self.read('data/labels/batch_state.json'))


if __name__ == '__main__':
    unittest.main()
