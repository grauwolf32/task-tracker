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


if __name__ == '__main__':
    unittest.main()
