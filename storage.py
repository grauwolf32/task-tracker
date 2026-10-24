"""Optimistic YAML edits. Call save_task under the server's snapshot lock.

File replacement is atomic, but an external editor does not share that lock;
version checks cannot provide a filesystem-wide compare-and-swap transaction.
"""
import hashlib
import io
import os
import stat
import tempfile
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

MAX_FILE_BYTES = 1024 * 1024
STATUSES = {'pending', 'in_progress', 'deferred', 'completed', 'archived'}


class EditError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def task_version(raw):
    return '"' + hashlib.sha256(raw.encode('utf-8')).hexdigest() + '"'


def read_yaml_file(root, path):
    """Read at most 1 MiB from an ordinary file inside the configured tree."""
    root, path = Path(root).resolve(), Path(path)
    if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Символические ссылки не поддерживаются')
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Ожидается обычный YAML-файл')
        payload = stream.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError('Файл больше 1 МиБ')
    return payload.decode('utf-8')


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def _remove_owned(path, identity, expected):
    """Cleanup must not unlink a replacement created by an external writer."""
    try:
        if _identity(path) == identity and task_version(read_yaml_file(path.parent, path)) == expected:
            path.unlink()
    except (OSError, ValueError):
        pass


def save_task(root, item, changes, expected):
    if not expected:
        raise EditError('Не указана версия файла. Перечитайте задачу.', 428)
    if not isinstance(changes, dict) or not changes or set(changes) - {'title', 'goal', 'status'}:
        raise EditError('Можно менять только title, goal и status')
    for key, value in changes.items():
        limit = 500 if key == 'title' else 64000 if key == 'goal' else 32
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise EditError(f'Недопустимое значение {key}')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise EditError(f'Недопустимый Unicode в {key}') from exc
        if key in {'title', 'status'} and not value.strip():
            raise EditError(f'Поле {key} обязательно')
    if 'status' in changes and changes['status'] not in STATUSES:
        raise EditError('Неизвестный статус')
    root = Path(root).resolve()
    path = root / item['file']
    try:
        original = read_yaml_file(root, path)
    except (FileNotFoundError, ValueError) as exc:
        raise EditError('Файл задачи удалён, изменён или недоступен для записи', 409) from exc
    if task_version(original) != expected:
        raise EditError('YAML изменился после открытия формы. Ваш черновик сохранён.', 409)
    codec = YAML()
    codec.preserve_quotes = True
    codec.width = 100
    try:
        data = codec.load(original)
    except Exception as exc:
        raise EditError('Не удалось прочитать YAML для редактирования', 409) from exc
    if not isinstance(data, dict) or data.get('id') != item['data']['id']:
        raise EditError('Содержимое задачи изменилось. Перечитайте файл.', 409)
    for key, value in changes.items():
        data[key] = LiteralScalarString(value) if key == 'goal' and '\n' in value else value
    output = io.StringIO()
    codec.dump(data, output)
    rendered = output.getvalue()
    if len(rendered.encode('utf-8')) > MAX_FILE_BYTES:
        raise EditError('Результат превышает 1 МиБ')
    destination = root / 'done' / path.name if data.get('status') in {'completed', 'archived'} else root / path.name
    if not destination.parent.resolve().is_relative_to(root) or destination.parent.is_symlink():
        raise EditError('Недопустимая папка назначения', 409)
    moving = destination != path
    if moving and (destination.exists() or destination.is_symlink()):
        raise EditError('В целевой папке уже есть файл с таким именем', 409)
    temporary = None
    owned_destination = None
    try:
        destination.parent.mkdir(exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.taskline-', suffix='.tmp', dir=destination.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        if task_version(read_yaml_file(root, path)) != expected:
            raise EditError('Файл изменился во время сохранения. Повторите после обновления.', 409)
        if moving:
            # Publish new content without ever overwriting a destination collision.
            # A failed unlink can leave two files; rollback only our own publication.
            os.link(temporary, destination)
            owned_destination = _identity(Path(temporary))
            if task_version(read_yaml_file(root, path)) != expected:
                raise EditError('Исходный файл изменился во время перемещения', 409)
            path.unlink()
        else:
            os.replace(temporary, destination)
            temporary = None
        return str(destination.relative_to(root))
    except (OSError, ValueError, EditError) as exc:
        if moving and owned_destination is not None:
            _remove_owned(destination, owned_destination, task_version(rendered))
        if isinstance(exc, EditError):
            raise
        if isinstance(exc, FileExistsError):
            raise EditError('В целевой папке появился другой файл. Перечитайте задачи.', 409) from exc
        raise EditError(f'Не удалось сохранить файл: {getattr(exc, "strerror", None) or str(exc)}', 500) from exc
    finally:
        if temporary:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
