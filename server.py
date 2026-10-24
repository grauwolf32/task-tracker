#!/usr/bin/env python3
"""A local viewer and editor for repository YAML tasks."""
import argparse
import hashlib
import datetime
import math
import threading
import json
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
import yaml
from storage import MAX_FILE_BYTES, STATUSES, save_task, task_version, read_yaml_file, EditError

PUBLIC = Path(__file__).parent / 'public'
MAX_FILES = 10000
MAX_TOTAL_BYTES = 48 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_VALUE_NODES = 50000
MAX_VALUE_DEPTH = 50


class StrictLoader(getattr(yaml, 'CSafeLoader', yaml.SafeLoader)):
    def construct_document(self, node):
        if isinstance(node, yaml.MappingNode):
            if any(key.tag != 'tag:yaml.org,2002:str' for key, _ in node.value):
                raise ValueError('Имена полей верхнего уровня должны быть строками')
        return super().construct_document(node)

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                if key is None or isinstance(key, (bool, int)):
                    key = str(key)
                elif isinstance(key, float) and math.isfinite(key):
                    key = str(key)
                elif isinstance(key, (datetime.date, datetime.datetime)):
                    key = key.isoformat()
                else:
                    raise ValueError('Ключи метаданных YAML должны быть конечными скалярными значениями')
            if key in result:
                raise ValueError(f'Повторяющийся ключ YAML: {key}')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def json_value(value):
    """Bound alias expansion and nesting before encoding JSON for the browser."""
    remaining = MAX_VALUE_NODES
    remaining_bytes = 4 * MAX_FILE_BYTES
    active = set()

    def convert(item, depth):
        nonlocal remaining, remaining_bytes
        remaining -= 1
        if remaining < 0 or depth > MAX_VALUE_DEPTH:
            raise ValueError('Слишком сложная структура YAML')
        if isinstance(item, str):
            remaining_bytes -= len(item.encode('utf-8'))
            if remaining_bytes < 0:
                raise ValueError('Развёрнутое содержимое YAML превышает 4 МиБ')
            return item
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError('Нечисловые значения NaN/Infinity не поддерживаются')
            return item
        if isinstance(item, (datetime.date, datetime.datetime)):
            return item.isoformat()
        if not isinstance(item, (dict, list)):
            raise ValueError('Неподдерживаемый тип значения YAML')
        identity = id(item)
        if identity in active:
            raise ValueError('Циклические ссылки YAML не поддерживаются')
        active.add(identity)
        try:
            if isinstance(item, list):
                return [convert(v, depth + 1) for v in item]
            if not all(isinstance(k, str) for k in item):
                raise ValueError('Ключи YAML должны быть строками')
            return {convert(k, depth + 1): convert(v, depth + 1) for k, v in item.items()}
        finally:
            active.remove(identity)

    return convert(value, 0)


def string_list(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_task(data):
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k].strip() for k in ('id', 'title')):
        raise ValueError('Ожидается YAML-объект с непустыми строковыми id и title')
    for key in ('status', 'priority', 'kind', 'epic', 'milestone', 'goal'):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f'Поле {key} должно быть строкой')
    for key, allowed in [('status', STATUSES), ('priority', {'P0', 'P1', 'P2', 'P3'}),
                         ('kind', {'feature', 'defect', 'chore'})]:
        if key in data and data[key] not in allowed:
            raise ValueError(f'Неизвестное значение {key}: {data[key]}')
    for key in ('depends_on', 'languages'):
        if key in data and not string_list(data[key]):
            raise ValueError(f'Поле {key} должно быть массивом строк')
    if 'tests' in data:
        if not isinstance(data['tests'], list):
            raise ValueError('Поле tests должно быть массивом')
        for test in data['tests']:
            if isinstance(test, str):
                continue
            if not isinstance(test, dict) or not isinstance(test.get('command'), str):
                raise ValueError('Проверка должна содержать строковую command')
            if 'covers' in test and not string_list(test['covers']):
                raise ValueError('Поле covers должно быть массивом строк')


def load_tasks(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f'Папка задач не найдена: {root}')
    files = sorted([*root.glob('*.yml'), *root.glob('*.yaml'),
                    *root.glob('done/*.yml'), *root.glob('done/*.yaml')])
    files = [p for p in files if p.name not in {'index.yml', 'index.yaml', 'epics.yml', 'epics.yaml'}]
    if len(files) > MAX_FILES:
        raise ValueError(f'Слишком много файлов: максимум {MAX_FILES}')
    tasks, errors, ids = [], [], {}
    total = response_size = 0
    complete = True
    for path in files:
        relative = str(path.relative_to(root))
        try:
            inspected_bytes = min(path.lstat().st_size, MAX_FILE_BYTES + 1)
            total += inspected_bytes
            if total > MAX_TOTAL_BYTES:
                raise ValueError('Общий размер файлов больше 48 МиБ')
            raw = read_yaml_file(root, path)
            total += max(0, len(raw.encode('utf-8')) - inspected_bytes)
            if total > MAX_TOTAL_BYTES:
                raise ValueError('Общий размер файлов больше 48 МиБ')
            data = json_value(yaml.load(raw, Loader=StrictLoader))
            if isinstance(data, dict) and isinstance(data.get('id'), str):
                ids.setdefault(data['id'], []).append(relative)
            validate_task(data)
            item = {'data': data, 'file': relative, 'raw': raw, 'version': task_version(raw)}
            response_size += len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
            if response_size > MAX_RESPONSE_BYTES:
                raise ValueError('Результат превышает 64 МиБ')
            tasks.append(item)
        except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError) as exc:
            errors.append({'file': relative, 'message': str(exc)[:400]})
        if total > MAX_TOTAL_BYTES or response_size > MAX_RESPONSE_BYTES:
            complete = False
            break
    ambiguous = {task_id for task_id, paths in ids.items() if len(paths) > 1}
    for task_id in ambiguous:
        errors.extend({'file': path, 'message': f'Повторяющийся ID: {task_id}'} for path in ids[task_id])
    # No copy of an ambiguous identity may be displayed or selected for editing.
    tasks = [t for t in tasks if t['data']['id'] not in ambiguous]
    epics = load_epics(root, errors)
    snapshot = {'tasks': tasks, 'epics': epics, 'errors': errors, 'source': str(root),
                'project': root.parent.name, 'complete': complete}
    if len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_RESPONSE_BYTES:
        raise ValueError('Результат превышает 64 МиБ')
    return snapshot


def source_revision(root):
    """Stat YAML paths, including metadata, without parsing unchanged task files."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f'Папка задач не найдена: {root}')
    paths = sorted({*root.glob('*.yml'), *root.glob('*.yaml'),
                    *root.glob('done/*.yml'), *root.glob('done/*.yaml')})
    if len(paths) > MAX_FILES + 4:
        raise ValueError(f'Слишком много файлов: максимум {MAX_FILES}')
    digest = hashlib.sha256()
    for path in paths:
        try:
            stat = path.lstat()
            stamp = (str(path.relative_to(root)), stat.st_ino, stat.st_size,
                     stat.st_mtime_ns, stat.st_ctime_ns)
        except FileNotFoundError:
            # An editor or git can rename a file between enumeration and stat.
            stamp = (str(path.relative_to(root)), 'removed')
        digest.update(repr(stamp).encode())
    return '"' + digest.hexdigest() + '"'


def load_epics(root, errors):
    epics, seen = [], set()
    for name in ('epics.yml', 'epics.yaml'):
        path = Path(root) / name
        if not path.exists():
            continue
        try:
            data = json_value(yaml.load(read_yaml_file(root, path), Loader=StrictLoader))
            if not isinstance(data, dict) or not isinstance(data.get('epics'), list):
                raise ValueError('Ожидается объект с массивом epics')
            pending = []
            local_ids = set()
            for epic in data['epics']:
                if not isinstance(epic, dict) or not all(isinstance(epic.get(k), str) for k in ('id', 'title')):
                    raise ValueError('У эпика должны быть строковые id и title')
                if epic['id'] in seen or epic['id'] in local_ids:
                    raise ValueError(f"Повторяющийся эпик: {epic['id']}")
                if 'scope' in epic and not isinstance(epic['scope'], str):
                    raise ValueError('Описание эпика должно быть строкой')
                local_ids.add(epic['id'])
                pending.append({'id': epic['id'], 'title': epic['title'],
                              'scope': epic.get('scope', '')})
            seen.update(local_ids)
            epics.extend(pending)
        except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError) as exc:
            errors.append({'file': name, 'message': str(exc)[:400]})
    return epics


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Повторяющийся ключ JSON: {key}')
        result[key] = value
    return result


def make_handler(root):
    cache = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def valid_origin(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            return host in hosts and (not origin or origin == f'http://{host}')

        def do_PUT(self):
            if not self.valid_origin():
                self.respond(403, 'application/json', b'{"error":"Invalid origin"}')
                return
            route = urlsplit(self.path).path
            if not route.startswith('/api/tasks/'):
                self.respond(404, 'application/json', b'{"error":"Not found"}')
                return
            try:
                if len(self.headers.get_all('Content-Length', [])) != 1 or self.headers.get('Transfer-Encoding'):
                    raise EditError('Ожидается один Content-Length без Transfer-Encoding', 400)
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 256 * 1024:
                    raise EditError('Недопустимый размер запроса', 413)
                if self.headers.get_content_type() != 'application/json':
                    raise EditError('Ожидается application/json', 415)
                payload = self.rfile.read(length)
                if len(payload) != length:
                    raise EditError('Тело запроса получено не полностью', 400)
                changes = json.loads(payload, object_pairs_hook=unique_json_object)
                task_id = unquote(route.removeprefix('/api/tasks/'))
                with lock:
                    snapshot = load_tasks(root)
                    if not snapshot['complete']:
                        raise EditError('Список файлов неполный из-за ограничений размера. Сохранение отключено.', 409)
                    item = next((t for t in snapshot['tasks'] if t['data']['id'] == task_id), None)
                    if item is None:
                        raise EditError('Задача отсутствует, YAML повреждён или ID не уникален.', 404)
                    file = save_task(root, item, changes, self.headers.get('If-Match'))
                    cache.clear()
                self.respond(200, 'application/json', json.dumps({'file': file}).encode())
            except EditError as exc:
                self.respond(exc.status, 'application/json', json.dumps({'error': str(exc)}).encode())
            except (ValueError, OSError, UnicodeError) as exc:
                self.respond(400, 'application/json', json.dumps({'error': str(exc)[:400]}).encode())

        def do_GET(self):
            if not self.valid_origin():
                self.respond(403, 'application/json', b'{"error":"Invalid origin"}')
                return
            route = urlsplit(self.path).path
            if route == '/api/tasks':
                try:
                    with lock:
                        revision = source_revision(root)
                        if cache.get('revision') != revision:
                            # Retain the pre-read revision: concurrent edits trigger another read.
                            payload = json.dumps(load_tasks(root), ensure_ascii=False).encode()
                            cache.update(revision=revision, payload=payload)
                        payload = cache['payload']
                    if self.headers.get('If-None-Match') == revision:
                        self.respond(304, 'application/json', b'', revision)
                    else:
                        self.respond(200, 'application/json; charset=utf-8', payload, revision)
                except (ValueError, OSError) as exc:
                    self.respond(400, 'application/json; charset=utf-8', json.dumps({'error': str(exc)}).encode())
                return
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/favicon.svg': 'favicon.svg'}.get(route)
            if not name:
                self.respond(404, 'text/plain', b'Not found')
                return
            path = PUBLIC / name
            self.respond(200, mimetypes.guess_type(name)[0] or 'text/plain', path.read_bytes())

        def respond(self, code, content_type, body, etag=None):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            if code != 304:
                self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache')
            if etag:
                self.send_header('ETag', etag)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if body:
                self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', type=Path, default=Path.home() / 'src/contractor/tasks', help='Path to a tasks directory')
    parser.add_argument('--port', type=int, default=4310)
    args = parser.parse_args()
    if not args.tasks.is_dir():
        parser.error(f'Папка не существует: {args.tasks}')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(args.tasks))
    print(f'Taskline → http://127.0.0.1:{server.server_port}', flush=True)
    print(f'Источник: {args.tasks.resolve()} (локальный редактор YAML)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
