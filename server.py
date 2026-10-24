#!/usr/bin/env python3
"""A local viewer and editor for repository YAML tasks."""
import argparse
import hashlib
import datetime
import math
import threading
import json
import mimetypes
import re
from collections import Counter
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
import yaml
from storage import (MAX_FILE_BYTES, STATUSES, save_task, create_task, render_new_task,
                     task_version, read_yaml_file, EditError)

PUBLIC = Path(__file__).parent / 'public'
MAX_FILES = 10000
MAX_TOTAL_BYTES = 48 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_VALUE_NODES = 50000
MAX_VALUE_DEPTH = 50
PRIORITIES = {'P0', 'P1', 'P2', 'P3'}
KINDS = {'feature', 'defect', 'chore'}
ID_PATTERN = r'[A-Z][A-Z0-9]*-[0-9]{3,}'
MAX_DEPENDENCIES = 100


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
    for key, allowed in [('status', STATUSES), ('priority', PRIORITIES), ('kind', KINDS)]:
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
    snapshot['creation'] = creation_options(root, snapshot)
    if len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_RESPONSE_BYTES:
        raise ValueError('Результат превышает 64 МиБ')
    return snapshot


def suggested_id(tasks):
    """A suggestion, never a reservation; all IDs are rechecked on POST."""
    parts = []
    for item in tasks:
        task_id = item['data']['id']
        if re.fullmatch(ID_PATTERN, task_id) and len(task_id) <= 64:
            prefix, number = task_id.rsplit('-', 1)
            parts.append((prefix, int(number), len(number)))
    if not parts:
        return 'T-001'
    counts = Counter(prefix for prefix, _, _ in parts)
    families = [re.fullmatch(r'([A-Z]+)([0-9]+)', prefix) for prefix in counts]
    if all(families) and len({match[1] for match in families}) == 1:
        prefix = max(counts, key=lambda value: (int(re.search(r'[0-9]+$', value)[0]), value))
    else:
        def natural(value):
            return tuple((0, int(piece)) if piece.isdigit() else (1, piece)
                         for piece in re.split(r'([0-9]+)', value) if piece)
        prefix = min(counts, key=lambda value: (-counts[value], natural(value), value))
    selected = [(number, width) for candidate, number, width in parts if candidate == prefix]
    number = max(number for number, _ in selected) + 1
    width = max(3, max(width for _, width in selected))
    candidate = f'{prefix}-{number:0{width}d}'
    # A valid existing ID can already consume the full 64-character budget.
    return candidate if len(candidate) <= 64 else ''


def creation_options(root, snapshot):
    catalog = any((Path(root) / name).exists() or (Path(root) / name).is_symlink()
                  for name in ('epics.yml', 'epics.yaml'))
    epics = snapshot['epics'] if catalog else [
        {'id': epic, 'title': epic, 'scope': ''}
        for epic in sorted({item['data']['epic'] for item in snapshot['tasks']
                            if item['data'].get('epic', '').strip()})]
    return {'suggested_id': suggested_id(snapshot['tasks']), 'epics': epics,
            'max_dependencies': MAX_DEPENDENCIES, 'id_pattern': ID_PATTERN}


def validate_creation(changes, snapshot):
    allowed = {'id', 'title', 'goal', 'epic', 'priority', 'kind', 'depends_on',
               'acceptance_criteria', 'tests'}
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise EditError('Недопустимые поля новой задачи')
    for key, limit in [('id', 64), ('title', 500), ('goal', 64000),
                       ('epic', MAX_FILE_BYTES), ('priority', 2), ('kind', 7)]:
        value = changes.get(key, 'feature' if key == 'kind' else None)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
            raise EditError(f'Недопустимое значение {key}')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise EditError(f'Недопустимый Unicode в {key}') from exc
    task_id = changes['id']
    if not re.fullmatch(ID_PATTERN, task_id):
        raise EditError('ID должен иметь вид T-001 или V414-001')
    if changes['priority'] not in PRIORITIES or changes.get('kind', 'feature') not in KINDS:
        raise EditError('Неизвестный приоритет или тип задачи')
    if changes['epic'] not in {epic['id'] for epic in snapshot['creation']['epics']}:
        raise EditError('Выберите эпик из каталога проекта или существующих задач')
    tasks = {item['data']['id']: item['data'] for item in snapshot['tasks']}
    if task_id.casefold() in {existing.casefold() for existing in tasks}:
        raise EditError('Задача с таким ID уже существует', 409)
    dependencies = changes.get('depends_on', [])
    if not string_list(dependencies) or len(dependencies) > MAX_DEPENDENCIES:
        raise EditError(f'Зависимости должны быть массивом из не более {MAX_DEPENDENCIES} ID')
    if len(dependencies) != len(set(dependencies)):
        raise EditError('Зависимости не должны повторяться')
    if task_id in dependencies:
        raise EditError('Задача не может зависеть от самой себя')
    if any(dependency not in tasks for dependency in dependencies):
        raise EditError('Одна из зависимостей больше не существует или имеет неоднозначный ID')
    visited, pending = set(), list(dependencies)
    while pending:
        dependency = pending.pop()
        if dependency == task_id:
            raise EditError('Новая задача создаёт цикл зависимостей')
        if dependency not in visited:
            visited.add(dependency)
            pending.extend(tasks.get(dependency, {}).get('depends_on', []))
    criteria, commands = changes.get('acceptance_criteria', []), changes.get('tests', [])
    for key, values in [('acceptance_criteria', criteria), ('tests', commands)]:
        if not string_list(values) or len(values) > 100:
            raise EditError(f'{key}: ожидается массив из не более 100 строк')
        for value in values:
            if not value.strip() or len(value) > 4000 or '\x00' in value:
                raise EditError(f'Недопустимая строка в {key}')
            try:
                value.encode('utf-8')
            except UnicodeEncodeError as exc:
                raise EditError(f'Недопустимый Unicode в {key}') from exc
    if bool(criteria) != bool(commands):
        raise EditError('Критерии приёмки и команды проверок нужно заполнить вместе')
    acceptance = [{'id': f'A{index + 1}', 'text': text} for index, text in enumerate(criteria)]
    return {'schema_version': '1.0', 'id': task_id, 'title': changes['title'],
            'status': 'pending', 'priority': changes['priority'], 'epic': changes['epic'],
            'kind': changes.get('kind', 'feature'), 'depends_on': dependencies,
            'goal': changes['goal'], 'scope': {'in': [changes['goal']], 'out': []},
            'acceptance_criteria': acceptance,
            'tests': [{'command': command, 'covers': [criterion['id'] for criterion in acceptance]}
                      for command in commands]}


def check_creation_capacity(root, snapshot, data, rendered):
    if len(snapshot['tasks']) >= MAX_FILES:
        raise EditError(f'Слишком много файлов: максимум {MAX_FILES}', 409)
    total = sum(len(item['raw'].encode('utf-8')) for item in snapshot['tasks'])
    if total + len(rendered.encode('utf-8')) > MAX_TOTAL_BYTES:
        raise EditError('Новая задача превысит общий размер файлов 48 МиБ', 409)
    item = {'data': data, 'file': data['id'].lower() + '.yml', 'raw': rendered,
            'version': task_version(rendered)}
    projected = {**snapshot, 'tasks': [*snapshot['tasks'], item]}
    projected['creation'] = creation_options(root, projected)
    if len(json.dumps(projected, ensure_ascii=False).encode('utf-8')) > MAX_RESPONSE_BYTES:
        raise EditError('Новая задача превысит размер ответа 64 МиБ', 409)


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
        if not path.exists() and not path.is_symlink():
            continue
        try:
            data = json_value(yaml.load(read_yaml_file(root, path), Loader=StrictLoader))
            if not isinstance(data, dict) or not isinstance(data.get('epics'), list):
                raise ValueError('Ожидается объект с массивом epics')
            pending = []
            local_ids = set()
            for epic in data['epics']:
                if not isinstance(epic, dict) or not all(isinstance(epic.get(k), str) and epic[k].strip() for k in ('id', 'title')):
                    raise ValueError('У эпика должны быть непустые строковые id и title')
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

        def read_json_body(self):
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
            try:
                return json.loads(payload, object_pairs_hook=unique_json_object)
            except RecursionError as exc:
                raise EditError('Слишком сложная структура JSON') from exc

        def do_POST(self):
            if not self.valid_origin():
                self.respond(403, 'application/json', b'{"error":"Invalid origin"}')
                return
            if urlsplit(self.path).path != '/api/tasks':
                self.respond(404, 'application/json', b'{"error":"Not found"}')
                return
            try:
                changes = self.read_json_body()
                with lock:
                    try:
                        revision = source_revision(root)
                        snapshot = load_tasks(root)
                    except (ValueError, OSError) as exc:
                        raise EditError('Не удалось проверить все файлы задач. Создание отключено.', 409) from exc
                    if not snapshot['complete'] or snapshot['errors']:
                        raise EditError('Исправьте ошибки YAML и перечитайте полный список перед созданием задачи.', 409)
                    data = validate_creation(changes, snapshot)
                    rendered = render_new_task(data)
                    check_creation_capacity(root, snapshot, data, rendered)
                    if source_revision(root) != revision:
                        raise EditError('Файлы изменились во время проверки. Перечитайте задачи.', 409)
                    file = create_task(root, data['id'], rendered)
                    cache.clear()
                self.respond(201, 'application/json', json.dumps({'id': data['id'], 'file': file}).encode())
            except EditError as exc:
                self.respond(exc.status, 'application/json', json.dumps({'error': str(exc)}).encode())
            except (ValueError, OSError, UnicodeError) as exc:
                self.respond(400, 'application/json', json.dumps({'error': str(exc)[:400]}).encode())

        def do_PUT(self):
            if not self.valid_origin():
                self.respond(403, 'application/json', b'{"error":"Invalid origin"}')
                return
            route = urlsplit(self.path).path
            if not route.startswith('/api/tasks/'):
                self.respond(404, 'application/json', b'{"error":"Not found"}')
                return
            try:
                changes = self.read_json_body()
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
