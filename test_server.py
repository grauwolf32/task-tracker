import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from server import load_tasks, make_handler


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'done').mkdir()
        (self.root / 'templates').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def test_loads_open_and_done_ignores_catalog_and_templates(self):
        self.put('a.yml', 'id: V1-001\ntitle: Открытая задача\nstatus: pending\ndepends_on: [V1-002]\n')
        self.put('done/b.yaml', 'id: V1-002\ntitle: Completed\nstatus: completed\n')
        self.put('index.yml', 'not: a task')
        self.put('templates/example.yml', 'id: TEMPLATE\ntitle: Ignore')
        result = load_tasks(self.root)
        self.assertEqual({t['data']['id'] for t in result['tasks']}, {'V1-001', 'V1-002'})
        self.assertEqual(result['errors'], [])
        self.assertIn('Открытая', result['tasks'][0]['raw'])
        self.assertEqual(result['tasks'][0]['data']['depends_on'], ['V1-002'])

    def test_invalid_duplicate_and_large_files_are_reported(self):
        self.put('a.yml', 'id: X\ntitle: Good')
        self.put('b.yml', 'id: X\ntitle: Duplicate')
        self.put('bad.yml', 'id: [')
        self.put('missing.yml', 'title: No ID')
        self.put('huge.yml', 'x' * (1024 * 1024 + 1))
        result = load_tasks(self.root)
        self.assertEqual(len(result['tasks']), 0)
        self.assertEqual(len(result['errors']), 5)

    def test_realistic_volume_and_max_count(self):
        for n in range(1000):
            self.put(f't{n:04}.yml', f'id: V1-{n}\ntitle: Task {n}\nstatus: pending')
        self.assertEqual(len(load_tasks(self.root)['tasks']), 1000)
        with patch('server.MAX_FILES', 999), self.assertRaises(ValueError):
            load_tasks(self.root)

    def test_symlinks_not_followed_and_dates_serialize(self):
        self.put('a.yml', 'id: X\ntitle: Dated\ndate: 2026-10-24')
        (self.root / 'link.yml').symlink_to(self.root / 'a.yml')
        result = load_tasks(self.root)
        self.assertEqual(result['tasks'][0]['data']['date'], '2026-10-24')
        self.assertEqual(len(result['errors']), 1)

    def test_http_read_refresh_static_and_missing_source(self):
        self.put('a.yml', 'id: X\ntitle: First')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/api/tasks') as response:
                self.assertEqual(json.load(response)['tasks'][0]['data']['title'], 'First')
                self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
            self.put('a.yml', 'id: X\ntitle: Changed')
            with urlopen(base + '/api/tasks') as response:
                self.assertEqual(json.load(response)['tasks'][0]['data']['title'], 'Changed')
            with urlopen(base) as response:
                self.assertIn(b'Taskline', response.read())
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/../../server.py')
            self.assertEqual(error.exception.code, 404)
            (self.root / 'a.yml').unlink()
            (self.root / 'done').rmdir()
            (self.root / 'templates').rmdir()
            self.root.rmdir()
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/api/tasks')
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

class LiveTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / 'task.yml'
        self.file.write_text('# Keep this comment\nid: T-1\ntitle: First\nstatus: pending\nepic: quality\ngoal: Original\n', encoding='utf-8')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method='GET', path='/api/tasks', data=None, headers=None):
        from urllib.request import Request
        request = Request(self.base + path, method=method,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = urlopen(request)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, response.headers, json.loads(raw) if raw else None

    def test_unchanged_is_304_without_reparsing(self):
        code, headers, data = self.request()
        self.assertEqual(code, 200)
        with patch('server.load_tasks', side_effect=AssertionError('must not reparse')):
            self.assertEqual(self.request(headers={'If-None-Match': headers['ETag']})[0], 304)
        self.file.write_text(self.file.read_text().replace('First', 'Other'))
        code, new_headers, data = self.request(headers={'If-None-Match': headers['ETag']})
        self.assertEqual(code, 200)
        self.assertNotEqual(headers['ETag'], new_headers['ETag'])
        self.assertEqual(data['tasks'][0]['data']['title'], 'Other')

    def test_create_move_delete_and_epic_catalog_change_revision(self):
        _, headers, _ = self.request()
        tag = headers['ETag']
        (self.root / 'done').mkdir()
        self.file.rename(self.root / 'done/task.yml')
        code, headers, data = self.request(headers={'If-None-Match': tag})
        self.assertEqual(code, 200)
        self.assertEqual(data['tasks'][0]['file'], 'done/task.yml')
        tag = headers['ETag']
        (self.root / 'epics.yml').write_text('epics:\n- id: quality\n  title: Quality engineering\n  scope: Tests and gates\n')
        code, headers, data = self.request(headers={'If-None-Match': tag})
        self.assertEqual(code, 200)
        self.assertEqual(data['epics'][0]['title'], 'Quality engineering')
        tag = headers['ETag']
        (self.root / 'done/task.yml').unlink()
        code, headers, data = self.request(headers={'If-None-Match': tag})
        self.assertEqual(code, 200)
        self.assertEqual(data['tasks'], [])
        self.file.write_text('id: NEW\ntitle: Added')
        self.assertEqual(self.request(headers={'If-None-Match': headers['ETag']})[2]['tasks'][0]['data']['id'], 'NEW')

    def test_edit_preserves_comments_moves_and_rejects_stale_version(self):
        item = self.request()[2]['tasks'][0]
        code, _, data = self.request('PUT', '/api/tasks/T-1',
                                    {'goal': 'Edited\nSecond line', 'status': 'completed'},
                                    {'If-Match': item['version']})
        self.assertEqual(code, 200)
        self.assertEqual(data['file'], 'done/task.yml')
        self.assertFalse(self.file.exists())
        destination = self.root / 'done/task.yml'
        self.assertIn('# Keep this comment', destination.read_text())
        self.assertIn('Second line', destination.read_text())
        code, _, _ = self.request('PUT', '/api/tasks/T-1', {'title': 'Stale'}, {'If-Match': item['version']})
        self.assertEqual(code, 409)
        updated = self.request()[2]['tasks'][0]
        self.assertEqual(updated['data']['title'], 'First')
        self.assertEqual(self.request('PUT', '/api/tasks/T-1', {'status': 'pending'}, {'If-Match': updated['version']})[0], 200)
        self.assertTrue(self.file.exists())
        self.assertFalse(destination.exists())

    def test_conflicting_external_edit_and_validation(self):
        item = self.request()[2]['tasks'][0]
        self.file.write_text(self.file.read_text().replace('First', 'External'))
        self.assertEqual(self.request('PUT', '/api/tasks/T-1', {'goal': 'Overwrite'}, {'If-Match': item['version']})[0], 409)
        self.assertIn('External', self.file.read_text())
        item = self.request()[2]['tasks'][0]
        for changes in ({'status': 'made-up'}, {'title': ''}, {'unknown': 1}, {'goal': ['bad']}, {'goal': '\ud800'}):
            self.assertEqual(self.request('PUT', '/api/tasks/T-1', changes, {'If-Match': item['version']})[0], 400)
        self.assertEqual(self.request('PUT', '/api/tasks/T-1', {'goal': 'x'})[0], 428)
        self.assertEqual(self.request('PUT', '/api/tasks/T-1', {'goal': 'x'}, {'Origin': 'https://evil.example'})[0], 403)

    def test_write_failure_retains_original_and_no_temp_files(self):
        item = self.request()[2]['tasks'][0]
        original = self.file.read_bytes()
        with patch('storage.os.link', side_effect=OSError('disk failure')):
            self.assertEqual(self.request('PUT', '/api/tasks/T-1', {'status': 'archived'}, {'If-Match': item['version']})[0], 500)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(list((self.root / 'done').iterdir()), [])


class ReaderRegressionTests(unittest.TestCase):
    setUp = TaskTests.setUp
    tearDown = TaskTests.tearDown
    put = TaskTests.put

    def test_duplicate_ids_hide_all_copies(self):
        self.put('one.yml', 'id: SAME\ntitle: One')
        self.put('two.yml', 'id: SAME\ntitle: Two')
        result = load_tasks(self.root)
        self.assertEqual(result['tasks'], [])
        self.assertEqual({e['file'] for e in result['errors']}, {'one.yml', 'two.yml'})

    def test_malformed_shapes_and_unsafe_yaml_values_are_isolated(self):
        cases = {
            'status': 'status: {bad: value}',
            'dependencies': 'depends_on: T-2',
            'languages': 'languages: [python, 2]',
            'tests': 'tests: null',
            'covers': 'tests: [{command: echo ok, covers: invalid}]',
            'numeric_field': '1: value',
            'complex_key': 'context: {? [a, b]: value}',
            'duplicate_key': 'goal: First\ngoal: Second',
            'nonfinite': 'context: {number: .nan}',
            'cyclic': 'context: &loop [*loop]',
        }
        for name, body in cases.items():
            self.put(name + '.yml', f'id: {name}\ntitle: Invalid\n{body}\n')
        self.put('valid.yml', 'id: OK\ntitle: Valid')
        result = load_tasks(self.root)
        self.assertEqual([t['data']['id'] for t in result['tasks']], ['OK'])
        self.assertEqual(len(result['errors']), len(cases))

    def test_nested_scalar_metadata_keys_are_normalized_without_collisions(self):
        self.put('scalar.yml', 'id: SCALAR\ntitle: Metadata\ncontext: {404: Missing, 0.1: Tolerance, false: Flag, null: Empty, 2026-10-25: Date}\n')
        result = load_tasks(self.root)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['tasks'][0]['data']['context'],
                         {'404': 'Missing', '0.1': 'Tolerance', 'False': 'Flag',
                          'None': 'Empty', '2026-10-25': 'Date'})
        for filename, metadata in [('number', '{1: Number, "1": String}'),
                                   ('boolean', '{false: Boolean, "False": String}'),
                                   ('date', '{2026-10-25: Date, "2026-10-25": String}')]:
            self.put(filename + '.yml', f'id: {filename}\ntitle: Collision\ncontext: {metadata}\n')
        result = load_tasks(self.root)
        self.assertEqual([t['data']['id'] for t in result['tasks']], ['SCALAR'])
        self.assertEqual(len(result['errors']), 3)

    def test_alias_expansion_and_nonregular_files_are_bounded(self):
        self.put('alias.yml', 'id: ALIAS\ntitle: Large\na: &a ' + 'x' * 16384 + '\ncontext: [' + ','.join(['*a'] * 512) + ']')
        os.mkfifo(self.root / 'pipe.yml')
        result = load_tasks(self.root)
        self.assertEqual(result['tasks'], [])
        self.assertEqual(len(result['errors']), 2)
        self.assertIn('4 МиБ', next(e['message'] for e in result['errors'] if e['file'] == 'alias.yml'))

    def test_response_limit_and_rejected_files_count_toward_read_budget(self):
        self.put('a.yml', 'id: X\ntitle: ' + 'x' * 1000)
        with patch('server.MAX_RESPONSE_BYTES', 100), self.assertRaises(ValueError):
            load_tasks(self.root)
        self.put('a.yml', 'x' * (1024 * 1024 + 1))
        self.put('b.yml', 'id: SECOND\ntitle: Not reached')
        with patch('server.MAX_TOTAL_BYTES', 1024):
            result = load_tasks(self.root)
            self.assertEqual(result['tasks'], [])
            self.assertEqual([e['file'] for e in result['errors']], ['a.yml'])

    def test_invalid_epic_catalog_has_no_partial_entries(self):
        self.put('epics.yml', 'epics:\n- id: one\n  title: One\n- id: one\n  title: Duplicate\n')
        result = load_tasks(self.root)
        self.assertEqual(result['epics'], [])
        self.assertEqual(len(result['errors']), 1)


class WriteRegressionTests(unittest.TestCase):
    setUp = LiveTaskTests.setUp
    tearDown = LiveTaskTests.tearDown
    request = LiveTaskTests.request

    def test_ambiguous_id_cannot_be_written(self):
        original = self.request()[2]['tasks'][0]
        (self.root / 'duplicate.yml').write_text(self.file.read_text())
        code, _, _ = self.request('PUT', '/api/tasks/T-1', {'title': 'Changed'}, {'If-Match': original['version']})
        self.assertEqual(code, 404)
        self.assertIn('title: First', self.file.read_text())
        self.assertEqual(self.request()[2]['tasks'], [])

    def test_move_never_overwrites_destination_appearing_during_save(self):
        original = self.request()[2]['tasks'][0]
        real_link = os.link
        target = self.root / 'done/task.yml'
        def collision(source, destination):
            target.write_text('External file')
            return real_link(source, destination)
        with patch('storage.os.link', side_effect=collision):
            code, _, _ = self.request('PUT', '/api/tasks/T-1', {'status': 'completed'}, {'If-Match': original['version']})
        self.assertEqual(code, 409)
        self.assertEqual(target.read_text(), 'External file')
        self.assertIn('status: pending', self.file.read_text())

    def test_failed_move_does_not_delete_newer_destination_content(self):
        original = self.request()[2]['tasks'][0]
        real_unlink = Path.unlink
        target = self.root / 'done/task.yml'
        def failed_unlink(path, *args, **kwargs):
            if path.resolve() == self.file.resolve():
                # An external writer changed the newly published destination in place.
                target.write_text('External update during cleanup')
                raise OSError('cannot remove source')
            return real_unlink(path, *args, **kwargs)
        with patch('storage.Path.unlink', new=failed_unlink):
            code, _, _ = self.request('PUT', '/api/tasks/T-1', {'status': 'completed'}, {'If-Match': original['version']})
        self.assertEqual(code, 500)
        self.assertEqual(target.read_text(), 'External update during cleanup')
        self.assertTrue(self.file.exists())

    def test_incomplete_snapshot_cannot_be_used_to_choose_a_write_target(self):
        original = self.request()[2]['tasks'][0]
        (self.root / 'z-duplicate.yml').write_text(self.file.read_text())
        with patch('server.MAX_TOTAL_BYTES', len(self.file.read_bytes()) + 1):
            code, _, _ = self.request('PUT', '/api/tasks/T-1', {'goal': 'unsafe'}, {'If-Match': original['version']})
        self.assertEqual(code, 409)
        self.assertIn('goal: Original', self.file.read_text())

    def test_duplicate_json_keys_are_rejected_and_304_has_no_false_length(self):
        from urllib.request import Request
        item = self.request()[2]['tasks'][0]
        request = Request(self.base + '/api/tasks/T-1', method='PUT',
                          data=b'{"goal":"one","goal":"two"}',
                          headers={'Content-Type': 'application/json', 'If-Match': item['version']})
        with self.assertRaises(HTTPError) as result:
            urlopen(request)
        self.assertEqual(result.exception.code, 400)
        result.exception.close()
        _, headers, _ = self.request()
        code, response_headers, _ = self.request(headers={'If-None-Match': headers['ETag']})
        self.assertEqual(code, 304)
        self.assertNotIn('Content-Length', response_headers)


class CreationMetadataTests(unittest.TestCase):
    setUp = TaskTests.setUp
    tearDown = TaskTests.tearDown
    put = TaskTests.put

    def test_suggestions_follow_numbered_family_and_other_prefix_frequency(self):
        self.assertEqual(load_tasks(self.root)['creation']['suggested_id'], 'T-001')
        for task_id in ('V2-009', 'V2-010', 'V10-001'):
            self.put(task_id + '.yml', f'id: {task_id}\ntitle: Existing\nepic: quality\n')
        self.assertEqual(load_tasks(self.root)['creation']['suggested_id'], 'V10-002')
        self.put('different.yml', 'id: OTHER-999\ntitle: Other\nepic: design\n')
        metadata = load_tasks(self.root)['creation']
        self.assertEqual(metadata['suggested_id'], 'V2-011')
        self.assertEqual([epic['id'] for epic in metadata['epics']], ['design', 'quality'])
        self.assertEqual(metadata['max_dependencies'], 100)
        self.assertEqual(metadata['id_pattern'], '[A-Z][A-Z0-9]*-[0-9]{3,}')

    def test_catalog_is_authoritative_even_when_empty(self):
        self.put('task.yml', 'id: T-001\ntitle: Old\nepic: old\n')
        self.put('epics.yml', 'epics:\n- id: current\n  title: Current area\n  scope: Scope\n')
        self.assertEqual(load_tasks(self.root)['creation']['epics'],
                         [{'id': 'current', 'title': 'Current area', 'scope': 'Scope'}])
        self.put('epics.yml', 'epics: []\n')
        self.assertEqual(load_tasks(self.root)['creation']['epics'], [])
        (self.root / 'epics.yml').unlink()
        (self.root / 'epics.yml').symlink_to(self.root / 'missing.yml')
        snapshot = load_tasks(self.root)
        self.assertEqual(snapshot['creation']['epics'], [])
        self.assertEqual(len(snapshot['errors']), 1)


class CreateTaskTests(unittest.TestCase):
    setUp = LiveTaskTests.setUp
    tearDown = LiveTaskTests.tearDown
    request = LiveTaskTests.request

    def payload(self, **changes):
        return {'id': 'T-002', 'title': 'Новая задача', 'goal': 'Описание\nВторая строка',
                'epic': 'quality', 'priority': 'P1', **changes}

    def create(self, **changes):
        return self.request('POST', '/api/tasks', self.payload(**changes))

    def test_create_persists_minimal_spec_refreshes_and_remains_editable(self):
        original = self.file.read_bytes()
        _, old_headers, _ = self.request()
        code, _, result = self.create(depends_on=['T-1'])
        self.assertEqual(code, 201)
        self.assertEqual(result, {'id': 'T-002', 'file': 't-002.yml'})
        code, headers, snapshot = self.request(headers={'If-None-Match': old_headers['ETag']})
        self.assertEqual(code, 200)
        self.assertNotEqual(headers['ETag'], old_headers['ETag'])
        self.assertEqual(snapshot['errors'], [])
        item = next(item for item in snapshot['tasks'] if item['data']['id'] == 'T-002')
        self.assertEqual(item['data'], {
            'schema_version': '1.0', 'id': 'T-002', 'title': 'Новая задача',
            'status': 'pending', 'priority': 'P1', 'epic': 'quality', 'kind': 'feature',
            'depends_on': ['T-1'], 'goal': 'Описание\nВторая строка',
            'scope': {'in': ['Описание\nВторая строка'], 'out': []},
            'acceptance_criteria': [], 'tests': []})
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(self.request('PUT', '/api/tasks/T-002', {'goal': 'Changed'},
                                     {'If-Match': item['version']})[0], 200)
        self.assertEqual(list(self.root.glob('.taskline-*')), [])

    def test_optional_criteria_and_commands_are_saved_without_inventing_checks(self):
        self.assertEqual(self.create(acceptance_criteria=['First criterion', 'Second criterion'],
                                     tests=['python check.py', 'npm test'], kind='chore')[0], 201)
        task = next(item['data'] for item in self.request()[2]['tasks'] if item['data']['id'] == 'T-002')
        self.assertEqual(task['acceptance_criteria'],
                         [{'id': 'A1', 'text': 'First criterion'}, {'id': 'A2', 'text': 'Second criterion'}])
        self.assertEqual(task['tests'], [{'command': 'python check.py', 'covers': ['A1', 'A2']},
                                        {'command': 'npm test', 'covers': ['A1', 'A2']}])
        self.assertEqual(task['kind'], 'chore')

    def test_validation_rejects_invalid_fields_and_dependency_shapes(self):
        invalid = [
            {'id': '../evil'}, {'id': 't-002'}, {'id': 'T-2'}, {'id': 'T-' + '0' * 63},
            {'title': ''}, {'title': 'x' * 501}, {'goal': ' '}, {'goal': 'x' * 64001},
            {'goal': '\x00'}, {'goal': '\ud800'}, {'priority': 'P4'}, {'epic': 'missing'},
            {'kind': 'other'}, {'status': 'completed'}, {'schema_version': '2.0'},
            {'depends_on': 'T-1'}, {'depends_on': ['T-1', 'T-1']}, {'depends_on': [None]},
            {'depends_on': ['T-002']}, {'depends_on': ['MISSING']}, {'depends_on': ['T-1'] * 101},
            {'acceptance_criteria': ['A']}, {'tests': ['test']},
            {'acceptance_criteria': [' '], 'tests': ['test']},
            {'acceptance_criteria': ['A'], 'tests': ['x' * 4001]},
            {'acceptance_criteria': ['A'] * 101, 'tests': ['test']},
            {'acceptance_criteria': ['A'], 'tests': ['\ud800']},
        ]
        for changes in invalid:
            with self.subTest(changes=str(changes)[:80]):
                self.assertEqual(self.create(**changes)[0], 400)
        for payload in (None, [], {'id': 'T-002'}):
            self.assertIn(self.request('POST', '/api/tasks', payload)[0], (400, 413))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['task.yml'])

    def test_known_dependencies_include_done_tasks_and_cycles_are_rejected(self):
        (self.root / 'done').mkdir()
        dependency = self.root / 'done/dependency.yml'
        dependency.write_text('id: T-003\ntitle: Done\nstatus: completed\n')
        self.assertEqual(self.create(depends_on=['T-1', 'T-003'])[0], 201)
        (self.root / 't-002.yml').unlink()
        dependency.write_text('id: T-003\ntitle: Done\nstatus: completed\ndepends_on: [T-002]\n')
        self.file.write_text(self.file.read_text() + 'depends_on: [T-003]\n')
        self.assertEqual(self.create(depends_on=['T-1'])[0], 400)
        self.assertFalse((self.root / 't-002.yml').exists())

    def test_long_dependency_chain_and_100_selected_dependencies(self):
        for number in range(1000):
            dependency = f'T-002' if number == 999 else f'DEP-{number + 1:03}'
            (self.root / f'dep-{number:03}.yml').write_text(
                f'id: DEP-{number:03}\ntitle: Dependency\ndepends_on: [{dependency}]\n')
        self.assertEqual(self.create(depends_on=['DEP-000'])[0], 400)
        (self.root / 'dep-999.yml').write_text('id: DEP-999\ntitle: Last\n')
        self.assertEqual(self.create(depends_on=[f'DEP-{number:03}' for number in range(100)])[0], 201)

    def test_duplicate_retry_and_concurrent_creates_preserve_first_publication(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        def attempt(title):
            barrier.wait()
            return title, self.create(title=title)[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ['First writer', 'Second writer']))
        self.assertEqual(sorted(status for _, status in results), [201, 409])
        winner = next(title for title, status in results if status == 201)
        original = (self.root / 't-002.yml').read_bytes()
        self.assertEqual(self.create(title='Retry after lost response')[0], 409)
        self.assertEqual((self.root / 't-002.yml').read_bytes(), original)
        self.assertIn(winner, original.decode())
        self.assertEqual(len(list(self.root.glob('t-002*'))), 1)

    def test_casefold_ids_and_filename_collisions_are_never_overwritten(self):
        (self.root / 'done').mkdir()
        for filename, task_id in [('other.yml', 't-002'), ('t-002.yml', 'OTHER'),
                                  ('done/t-002.yaml', 'OTHER'), ('done/T-002.YML', 'OTHER')]:
            with self.subTest(filename=filename):
                collision = self.root / filename
                collision.write_text(f'id: {task_id}\ntitle: Existing\n')
                original = collision.read_bytes()
                self.assertEqual(self.create()[0], 409)
                self.assertEqual(collision.read_bytes(), original)
                collision.unlink()
        (self.root / 't-002.yml').symlink_to(self.root / 'absent.yml')
        self.assertEqual(self.create()[0], 409)
        self.assertTrue((self.root / 't-002.yml').is_symlink())

    def test_invalid_or_incomplete_snapshots_and_capacity_reject_creation(self):
        bad = self.root / 'bad.yml'
        bad.write_text('id: [')
        self.assertEqual(self.create()[0], 409)
        bad.unlink()
        for constant, limit in [('MAX_TOTAL_BYTES', 1), ('MAX_FILES', 1),
                                ('MAX_RESPONSE_BYTES', 100)]:
            with self.subTest(constant=constant), patch('server.' + constant, limit):
                self.assertEqual(self.create()[0], 409)
        with patch('server.MAX_TOTAL_BYTES', len(self.file.read_bytes()) + 10):
            self.assertEqual(self.create()[0], 409)
        self.assertFalse((self.root / 't-002.yml').exists())

    def test_catalog_controls_choices_and_empty_project_requires_catalog(self):
        catalog = self.root / 'epics.yml'
        catalog.write_text('epics:\n- id: delivery\n  title: Delivery\n')
        self.assertEqual(self.create()[0], 400)
        self.assertEqual(self.create(epic='delivery')[0], 201)
        (self.root / 't-002.yml').unlink()
        self.file.unlink()
        catalog.unlink()
        self.assertEqual(self.create()[0], 400)
        catalog.write_text('epics:\n- id: quality\n  title: Quality\n')
        self.assertEqual(self.create()[0], 201)

    def test_failed_or_racing_publication_cleans_temp_and_keeps_existing_files(self):
        original = self.file.read_bytes()
        for operation in ('fsync', 'link'):
            with self.subTest(operation=operation), patch('storage.os.' + operation, side_effect=OSError('disk failure')):
                self.assertEqual(self.create()[0], 500)
            self.assertFalse((self.root / 't-002.yml').exists())
            self.assertEqual(list(self.root.glob('.taskline-*')), [])
        real_link = os.link
        target = self.root / 't-002.yml'
        def collision(source, destination):
            target.write_text('External file')
            return real_link(source, destination)
        with patch('storage.os.link', side_effect=collision):
            self.assertEqual(self.create()[0], 409)
        self.assertEqual(target.read_text(), 'External file')
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.taskline-*')), [])

    def test_create_uses_origin_media_type_and_json_framing_protections(self):
        from http.client import HTTPConnection
        for headers, expected in [({'Origin': 'https://evil.example'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415)]:
            self.assertEqual(self.request('POST', '/api/tasks', self.payload(), headers)[0], expected)
        for headers, body in [([('Content-Length', '2'), ('Content-Length', '2')], b'{}'),
                              ([('Transfer-Encoding', 'chunked')], b'0\r\n\r\n'),
                              ([('Content-Length', '15')], b'{"id":1,"id":2}')]:
            connection = HTTPConnection('127.0.0.1', self.server.server_port)
            try:
                connection.putrequest('POST', '/api/tasks')
                connection.putheader('Content-Type', 'application/json')
                for key, value in headers:
                    connection.putheader(key, value)
                connection.endheaders(body)
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
