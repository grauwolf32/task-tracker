import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml
import taskctl


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.root = self.repo / 'tasks'
        assets = Path(__file__).resolve().parents[1] / 'assets/tracker'
        shutil.copytree(assets, self.root)
        (self.root / 'done').mkdir()
        self.policy = yaml.safe_load((self.root / 'index.yml').read_text())
        self.policy['project']['name'] = 'Example'
        self.policy['execution_policy']['require_implementation_commit'] = False
        self.write_policy()
        (self.root / 'epics.yml').write_text(yaml.safe_dump({'epics': [{'id': 'core', 'title': 'Core', 'scope': 'Core application'}]}))
        self.base = {'schema_version': '1.0', 'id': 'T-001', 'title': 'Keep changes observable',
                     'epic': 'core', 'kind': 'feature', 'priority': 'P2', 'status': 'pending',
                     'depends_on': [], 'goal': 'A concrete outcome', 'scope': {'in': ['Boundary behavior'], 'out': []},
                     'acceptance_criteria': [{'id': 'A1', 'text': 'The result is observable'}],
                     'tests': [{'command': 'python -m unittest', 'covers': ['A1']}]}

    def tearDown(self):
        self.temp.cleanup()

    def write_policy(self):
        (self.root / 'index.yml').write_text(yaml.safe_dump(self.policy))

    def write_task(self, task_id='T-001', **changes):
        data = copy.deepcopy(self.base)
        data.update(changes, id=task_id)
        directory = self.root / 'done' if data['status'] in ('completed', 'archived') else self.root
        path = directory / (task_id.lower() + '.yml')
        path.write_text(yaml.safe_dump(data))
        return path, data

    def completed(self, task_id='T-001', **changes):
        return self.write_task(task_id, status='completed', completion={
            'summary': 'Implemented the result', 'verified_at': '2026-10-25',
            'tests': [{'command': 'python -m unittest', 'result': 'passed', 'evidence': 'Four tests passed with no skips'}]}, **changes)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(Path(taskctl.__file__)), *args, '--tasks', str(self.root)], capture_output=True, text=True)

    def test_bootstrap_and_deterministic_backlog(self):
        self.write_task(title='Render <safe> | title')
        self.assertEqual(len(taskctl.validate(self.root)), 1)
        self.assertNotEqual(self.cli('backlog', '--check').returncode, 0)
        self.assertEqual(self.cli('backlog').returncode, 0)
        original = (self.root / 'BACKLOG.md').read_bytes()
        self.assertIn(b'&lt;safe&gt; \\| title', original)
        self.assertEqual(self.cli('backlog', '--check').returncode, 0)
        self.assertEqual(self.cli('backlog').returncode, 0)
        self.assertEqual((self.root / 'BACKLOG.md').read_bytes(), original)
        self.write_task(title='Changed')
        self.assertNotEqual(self.cli('backlog', '--check').returncode, 0)

    def test_existing_custom_tracker_and_manual_backlog_are_preserved(self):
        self.write_task()
        (self.root / 'BACKLOG.md').write_text('Human notes\n')
        self.assertNotEqual(self.cli('backlog').returncode, 0)
        self.assertEqual((self.root / 'BACKLOG.md').read_text(), 'Human notes\n')
        del self.policy['tracker_format']
        self.write_policy()
        with self.assertRaisesRegex(taskctl.Invalid, 'existing project tools'):
            taskctl.validate(self.root)

    def test_duplicate_keys_ids_and_wrong_location(self):
        path, _ = self.write_task()
        path.write_text(path.read_text() + 'title: Another title\n')
        with self.assertRaisesRegex(taskctl.Invalid, 'unique strings'):
            taskctl.validate(self.root)
        path, _ = self.write_task()
        shutil.copy(path, self.root / 't-001-duplicate.yml')
        with self.assertRaisesRegex(taskctl.Invalid, 'duplicate id'):
            taskctl.validate(self.root)
        (self.root / 't-001-duplicate.yml').unlink()
        path.rename(self.root / 'done' / path.name)
        with self.assertRaisesRegex(taskctl.Invalid, 'location'):
            taskctl.validate(self.root)

    def test_missing_and_cyclic_dependencies(self):
        self.write_task(depends_on=['T-002'])
        with self.assertRaisesRegex(taskctl.Invalid, 'unknown dependency'):
            taskctl.validate(self.root)
        self.write_task('T-002', depends_on=['T-001'])
        with self.assertRaisesRegex(taskctl.Invalid, 'cycle'):
            taskctl.validate(self.root)

    def test_only_completed_dependencies_make_in_progress_ready(self):
        self.write_task(status='in_progress', depends_on=['T-002'])
        self.write_task('T-002')
        with self.assertRaisesRegex(taskctl.Invalid, 'not completed'):
            taskctl.validate(self.root)
        (self.root / 't-002.yml').unlink()
        self.write_task('T-002', status='archived', disposition={'reason': 'Dropped', 'date': '2026-10-25', 'previous_status': 'pending', 'reopen_when': 'Explicit new scope'})
        with self.assertRaisesRegex(taskctl.Invalid, 'not completed'):
            taskctl.validate(self.root)
        (self.root / 'done/t-002.yml').unlink()
        self.completed('T-002')
        self.assertEqual(len(taskctl.validate(self.root)), 2)

    def test_declared_acceptance_and_evidence_must_match(self):
        self.write_task(tests=[{'command': 'python -m unittest', 'covers': ['A2']}])
        with self.assertRaisesRegex(taskctl.Invalid, 'covers'):
            taskctl.validate(self.root)
        (self.root / 't-001.yml').unlink()
        path, data = self.completed()
        data['completion']['tests'][0]['result'] = 'skipped'
        path.write_text(yaml.safe_dump(data))
        with self.assertRaisesRegex(taskctl.Invalid, 'passed result'):
            taskctl.validate(self.root)

    def test_completion_commit_must_already_exist(self):
        self.policy['execution_policy']['require_implementation_commit'] = True
        self.write_policy()
        self.completed(implementation_commit='a' * 40)
        with self.assertRaisesRegex(taskctl.Invalid, 'existing commit'):
            taskctl.validate(self.root)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'implementation.txt').write_text('Delivered behavior\n')
        subprocess.run(['git', '-C', str(self.repo), 'add', 'implementation.txt'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '--no-gpg-sign', '-m', 'feat: implement behavior'], check=True)
        commit = subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()
        self.completed(implementation_commit=commit)
        self.assertEqual(len(taskctl.validate(self.root)), 1)

    def test_milestone_and_provenance_references(self):
        self.write_task(milestone='first', introduced_by='T-099')
        with self.assertRaisesRegex(taskctl.Invalid, 'introduced_by'):
            taskctl.validate(self.root)
        self.write_task('T-099')
        self.policy['milestones'] = [{'id': 'first', 'epic': 'core', 'outcome': 'First release', 'exit_task': 'T-002'}]
        self.write_policy()
        with self.assertRaisesRegex(taskctl.Invalid, 'exit_task'):
            taskctl.validate(self.root)
        self.write_task('T-002', depends_on=['T-001'], milestone='first')
        self.assertEqual(len(taskctl.validate(self.root)), 3)

    def test_milestone_tasks_and_exit_share_owner_and_membership(self):
        (self.root / 'epics.yml').write_text(yaml.safe_dump({'epics': [
            {'id': 'core', 'title': 'Core', 'scope': 'Core application'},
            {'id': 'operations', 'title': 'Operations', 'scope': 'Delivery infrastructure'}]}))
        self.policy['milestones'] = [{'id': 'first', 'epic': 'core', 'outcome': 'First release', 'exit_task': 'T-002'}]
        self.write_policy()
        self.write_task(milestone='first', epic='operations')
        self.write_task('T-002', milestone='first')
        with self.assertRaisesRegex(taskctl.Invalid, 'task epic must match milestone epic'):
            taskctl.validate(self.root)
        self.write_task(milestone='first')
        self.write_task('T-002', milestone='first', epic='operations')
        with self.assertRaisesRegex(taskctl.Invalid, 'exit_task epic must match milestone epic'):
            taskctl.validate(self.root)
        self.write_task('T-002')
        with self.assertRaisesRegex(taskctl.Invalid, 'exit_task must belong'):
            taskctl.validate(self.root)
        self.write_task('T-002', milestone='first')
        self.assertEqual(len(taskctl.validate(self.root)), 2)

    def test_structural_type_mutations_report_validation_errors(self):
        fields = ('id', 'title', 'goal', 'status', 'kind', 'priority', 'epic',
                  'depends_on', 'scope', 'acceptance_criteria', 'tests',
                  'milestone', 'introduced_by')
        for field in fields:
            for malformed in (None, [], {}, [1], {'x': 2}, 123, True):
                with self.subTest(field=field, value=malformed):
                    data = copy.deepcopy(self.base)
                    data[field] = malformed
                    (self.root / 't-001.yml').write_text(yaml.safe_dump(data))
                    try:
                        taskctl.validate(self.root)
                    except taskctl.Invalid:
                        pass
        self.write_task()
        for field in ('project', 'execution_policy', 'milestones'):
            original = copy.deepcopy(self.policy)
            for malformed in (None, [], {}, [1], {'x': 2}, 123, True):
                with self.subTest(policy_field=field, value=malformed):
                    self.policy = {**original, field: malformed}
                    self.write_policy()
                    try:
                        taskctl.validate(self.root)
                    except taskctl.Invalid:
                        pass
            self.policy = original
            self.write_policy()
        (self.root / 'epics.yml').write_text('epics: [null, [], {id: []}]')
        with self.assertRaises(taskctl.Invalid):
            taskctl.validate(self.root)

    def test_symlinks_and_malformed_values_fail_cleanly(self):
        path, _ = self.write_task()
        (self.root / 't-002.yml').symlink_to(path)
        with self.assertRaisesRegex(taskctl.Invalid, 'regular YAML'):
            taskctl.validate(self.root)
        (self.root / 't-002.yml').unlink()
        self.write_task(status='deferred', disposition={'reason': 'Paused', 'date': '2026-10-25', 'previous_status': [], 'reopen_when': 'Review'})
        self.assertNotEqual(self.cli('validate').returncode, 0)
        self.assertNotIn('Traceback', self.cli('validate').stderr)

    def test_deep_chain_does_not_use_recursive_dfs(self):
        for n in range(1100):
            self.write_task(f'T-{n:04}', depends_on=[f'T-{n-1:04}'] if n else [])
        self.assertEqual(len(taskctl.validate(self.root)), 1100)


if __name__ == '__main__':
    unittest.main()
