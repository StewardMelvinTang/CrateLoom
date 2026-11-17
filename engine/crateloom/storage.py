"""Where CrateLoom keeps its library: the database, downloads, set audio, artwork and caches.

The location is read once when the engine starts. ``CRATELOOM_DATA`` wins (tests and previews use it);
otherwise a small pointer file in Application Support names the folder you chose; otherwise the
library stays in ``~/.crateloom``. Moving copies everything to the new folder, rewrites the stored
paths that point inside the old folder, checks the copy and only then switches. The old folder is
kept until you delete it yourself, and the engine restarts on the new location.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

CONFIG = Path(os.environ.get('CRATELOOM_STORAGE_CONFIG') or Path.home() / 'Library' / 'Application Support' / 'CrateLoom' / 'storage.json')
DEFAULT = Path.home() / '.crateloom'
FOLDER = 'CrateLoom Library'
DATABASE = 'library.sqlite3'
CHUNK = 8 * 1024 * 1024
# Some room on top of the library itself, so the drive is not filled to the last byte.
HEADROOM = 512 * 1024 * 1024
SKIPPED = {DATABASE + '-journal', DATABASE + '-wal', DATABASE + '-shm', '.DS_Store'}
GROUPS = (('downloads', 'Downloads'), ('set-audio', 'DJ set audio'), ('analysis-cache', 'Analysis'),
          ('playback-cache', 'Playback cache'), ('artwork', 'Artwork'))
METHODS = ('storage.status', 'storage.check', 'storage.move', 'storage.use', 'storage.cancel', 'storage.discard_previous')
MOVE = {}
MOVE_LOCK = threading.Lock()


def read_config():
    try:
        value = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_config(value):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_name(f'.{CONFIG.name}.{uuid.uuid4().hex}')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(temporary, CONFIG)


def configured():
    """(root, source, available). A chosen folder that is missing, such as a disconnected drive, is never created."""
    environment = os.environ.get('CRATELOOM_DATA')
    if environment:
        return Path(environment), 'environment', True
    chosen = read_config().get('data')
    if isinstance(chosen, str) and chosen.startswith('/'):
        path = Path(chosen)
        return path, 'chosen', path.is_dir()
    return DEFAULT, 'default', True


def volume(path):
    """The drive a path lives on: the Mac's own disk, or the name of an external volume."""
    parts = Path(path).parts
    if len(parts) > 2 and parts[1] == 'Volumes':
        return parts[2], True
    return 'This Mac', False


def existing_parent(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def free_space(path):
    try:
        return shutil.disk_usage(existing_parent(path)).free
    except OSError:
        return None


def ignored(name):
    return name in SKIPPED or name.startswith('._')


def measure(root):
    """Total bytes and files under ``root``, with the large folders broken out."""
    groups = {key: 0 for key, _ in GROUPS}
    total = files = 0
    root = Path(root)
    for folder, directories, names in os.walk(root):
        directories[:] = [name for name in directories if not name.startswith('.CrateLoom Library-moving')]
        top = Path(folder).relative_to(root).parts[:1]
        for name in names:
            if ignored(name):
                continue
            try:
                size = os.stat(os.path.join(folder, name)).st_size
            except OSError:
                continue
            total += size
            files += 1
            if top and top[0] in groups:
                groups[top[0]] += size
    other = total - sum(groups.values())
    return {'bytes': total, 'files': files,
            'groups': [{'label': label, 'bytes': groups[key]} for key, label in GROUPS if groups[key]] + ([{'label': 'Library and other files', 'bytes': other}] if other else [])}


def describe_library(path):
    """Track count and last change of the CrateLoom library in ``path``, read without changing it."""
    database = Path(path) / DATABASE
    try:
        db = sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=2)
        try:
            tracks = db.execute('SELECT count(*) FROM tracks').fetchone()[0]
        finally:
            db.close()
    except sqlite3.Error:
        tracks = None
    try:
        changed = database.stat().st_mtime
    except OSError:
        changed = None
    return {'tracks': tracks, 'changed_at': changed}


def related(one, other):
    one, other = Path(one).resolve(), Path(other).resolve()
    return one == other or one in other.parents or other in one.parents


def status(api):
    root = Path(api.ROOT)
    available = getattr(api, 'STORAGE_AVAILABLE', True)
    name, external = volume(root)
    config = read_config()
    previous = config.get('previous')
    previous_info = None
    if available and isinstance(previous, str) and previous.startswith('/') and (Path(previous) / DATABASE).is_file() and not related(previous, root):
        previous_info = {'path': previous, 'volume': volume(previous)[0], 'bytes': measure(previous)['bytes']}
    with MOVE_LOCK:
        moving = dict(MOVE) if MOVE else None
    if moving:
        moving.pop('cancel', None)
    return {'path': str(root), 'source': getattr(api, 'STORAGE_SOURCE', 'default'), 'available': available,
            'volume': name, 'external': external, 'default_path': str(DEFAULT),
            'usage': measure(root) if available and not moving else None,
            'free': free_space(root) if available else None,
            'previous': previous_info, 'moving': moving}


def bad_text(path):
    # Stored paths sit inside JSON text; these characters would need escaping there.
    return any(character in path for character in '"\\') or any(ord(character) < 32 for character in path)


def plan(api, chosen=None, default=False):
    """What choosing a folder would do: move the library into it, or switch to a library already there."""
    root = Path(api.ROOT)
    if getattr(api, 'STORAGE_SOURCE', 'default') == 'environment':
        raise ValueError('This CrateLoom was started with CRATELOOM_DATA, so its library folder cannot be changed here.')
    if default:
        target = DEFAULT
    else:
        if not isinstance(chosen, str) or not chosen.startswith('/') or len(chosen) > 1024:
            raise ValueError('Choose a folder.')
        folder = Path(chosen)
        if not folder.is_dir():
            raise ValueError('That folder is not available. Check that the drive is connected.')
        folder = folder.resolve()
        target = folder if (folder / DATABASE).is_file() else folder / FOLDER
    target = Path(os.path.abspath(target))
    result = {'target': str(target), 'volume': volume(target)[0], 'external': volume(target)[1]}
    if bad_text(str(target)):
        return dict(result, mode=None, problem='Choose a folder whose name has no quotation marks or backslashes.')
    if related(target, root):
        same = target.resolve() == root.resolve()
        return dict(result, mode=None, problem='CrateLoom already keeps its library here.' if same else 'Choose a folder outside the current library folder.')
    if (target / DATABASE).is_file():
        return dict(result, mode='existing', library=describe_library(target), problem=None)
    if target.exists() and (not target.is_dir() or any(not ignored(entry.name) for entry in target.iterdir())):
        return dict(result, mode=None, problem=f'“{target.name}” already exists there and is not empty. Choose another folder.')
    if not os.access(existing_parent(target), os.W_OK):
        return dict(result, mode=None, problem='CrateLoom cannot write to that folder. Choose another one.')
    usage = measure(root)
    free = free_space(target)
    problem = None
    if free is not None and free < usage['bytes'] + HEADROOM:
        problem = 'There is not enough free space there for the library.'
    return dict(result, mode='move', needed=usage['bytes'], files=usage['files'], free=free, problem=problem)


def rewrite_paths(db, old_roots, new_root):
    """Point every stored path inside an old library folder at the same file in the new one."""
    new = str(new_root)
    pairs = []
    for old in dict.fromkeys(str(path) for path in old_roots):
        pairs.append((old + '/', new + '/'))
        escaped_old, escaped_new = json.dumps(old)[1:-1], json.dumps(new)[1:-1]
        if escaped_old != old or escaped_new != new:
            pairs.append((escaped_old + '/', escaped_new + '/'))
    changed = 0
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        for column in columns:
            for old in dict.fromkeys(str(path) for path in old_roots):
                changed += db.execute(f'UPDATE "{table}" SET "{column}"=? WHERE typeof("{column}")=\'text\' AND "{column}"=?', (new, old)).rowcount
            for old, replacement in pairs:
                changed += db.execute(f'UPDATE "{table}" SET "{column}"=replace("{column}",?,?) WHERE typeof("{column}")=\'text\' AND instr("{column}",?)>0',
                                      (old, replacement, old)).rowcount
    return changed


def progress(**fields):
    with MOVE_LOCK:
        MOVE.update(fields)


def copy_file(source, destination, cancel):
    with open(source, 'rb') as reader, open(destination, 'wb') as writer:
        while True:
            if cancel.is_set():
                raise InterruptedError
            chunk = reader.read(CHUNK)
            if not chunk:
                break
            writer.write(chunk)
            with MOVE_LOCK:
                MOVE['copied'] = MOVE.get('copied', 0) + len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    try:
        stat = os.stat(source)
        os.utime(destination, (stat.st_atime, stat.st_mtime))
    except OSError:
        pass


def copy_library(root, staging, cancel):
    count = 0
    for folder, directories, names in os.walk(root):
        directories[:] = [name for name in directories if not name.startswith('.CrateLoom Library-moving')]
        relative = Path(folder).relative_to(root)
        (staging / relative).mkdir(parents=True, exist_ok=True)
        for name in names:
            if ignored(name) or (relative == Path('.') and name == DATABASE):
                continue
            source = Path(folder) / name
            if not source.is_file():
                continue
            copy_file(source, staging / relative / name, cancel)
            count += 1
            progress(files_done=count, current=str(relative / name) if str(relative) != '.' else name)
    return count


def verify(root, staging):
    """The copy holds every file, at the same size, and its database is intact."""
    for folder, directories, names in os.walk(root):
        directories[:] = [name for name in directories if not name.startswith('.CrateLoom Library-moving')]
        relative = Path(folder).relative_to(root)
        for name in names:
            if ignored(name) or (relative == Path('.') and name == DATABASE):
                continue
            source = Path(folder) / name
            if not source.is_file():
                continue
            copied = staging / relative / name
            if not copied.is_file() or copied.stat().st_size != source.stat().st_size:
                raise ValueError(f'The copy of {relative / name} does not match. Nothing was switched.')
    db = sqlite3.connect(staging / DATABASE)
    try:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('The copied library database did not pass its check. Nothing was switched.')
    finally:
        db.close()


def run_move(api, target, stop_background):
    root = Path(api.ROOT)
    staging = target.parent / f'.{FOLDER}-moving-{uuid.uuid4().hex[:8]}'
    cancel = MOVE['cancel']
    try:
        progress(stage='Stopping background work')
        stop_background()
        progress(stage='Copying')
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        copy_library(root, staging, cancel)
        if cancel.is_set():
            raise InterruptedError
        progress(stage='Copying the library database', current=DATABASE)
        source = sqlite3.connect(api.DB, timeout=30)
        copy = sqlite3.connect(staging / DATABASE)
        try:
            source.backup(copy)
            progress(stage='Updating stored file locations')
            with copy:
                rewrite_paths(copy, {root, root.resolve()}, target)
        finally:
            copy.close()
            source.close()
        progress(stage='Checking the copy')
        verify(root, staging)
        if target.exists():
            target.rmdir()  # plan() allowed only an empty folder here
        os.rename(staging, target)
        config = read_config()
        if target.resolve() == DEFAULT.resolve() and not os.environ.get('CRATELOOM_DATA'):
            config.pop('data', None)
        else:
            config['data'] = str(target)
        config['previous'] = str(root)
        config['moved_at'] = time.time()
        write_config(config)
        progress(state='done', stage='Moved', current=None)
    except InterruptedError:
        shutil.rmtree(staging, ignore_errors=True)
        progress(state='cancelled', stage='Cancelled', current=None)
    except Exception as error:  # the library stays where it was
        shutil.rmtree(staging, ignore_errors=True)
        progress(state='failed', stage='Failed', current=None, error=str(error) or 'The move failed. Your library was not changed.')


def start_move(api, chosen, default, stop_background):
    found = plan(api, chosen, default)
    if found['problem']:
        raise ValueError(found['problem'])
    if found['mode'] != 'move':
        raise ValueError('That folder already holds a CrateLoom library. Use it instead, or choose another folder.')
    with MOVE_LOCK:
        if MOVE.get('state') == 'running':
            raise ValueError('The library is already being moved.')
        MOVE.clear()
        MOVE.update(state='running', stage='Starting', target=found['target'], copied=0, total=found['needed'],
                    files_done=0, files=found['files'], error=None, current=None, cancel=threading.Event())
    threading.Thread(target=run_move, args=(api, Path(found['target']), stop_background), name='storage-move', daemon=True).start()
    return status(api)


def use_existing(api, chosen, default):
    found = plan(api, chosen, default)
    if found['problem']:
        raise ValueError(found['problem'])
    # With the chosen drive gone, you may also start over in the Mac's default folder.
    fresh = default and found['mode'] == 'move' and not getattr(api, 'STORAGE_AVAILABLE', True)
    if found['mode'] != 'existing' and not fresh:
        raise ValueError('There is no CrateLoom library in that folder.')
    config = read_config()
    target = Path(found['target'])
    if target.resolve() == DEFAULT.resolve():
        config.pop('data', None)
    else:
        config['data'] = str(target)
    config.pop('previous', None)
    write_config(config)
    with MOVE_LOCK:
        MOVE.clear()
        MOVE.update(state='done', stage='Switched', target=str(target), error=None)
    return status(api)


def discard_previous(api):
    """Delete the folder the library was moved away from, once the new one is in use."""
    root = Path(api.ROOT)
    previous = read_config().get('previous')
    if not getattr(api, 'STORAGE_AVAILABLE', True) or not (root / DATABASE).is_file():
        raise ValueError('The current library is not available, so the earlier copy is kept.')
    if not isinstance(previous, str) or not previous.startswith('/'):
        raise ValueError('There is no earlier copy to delete.')
    folder = Path(previous)
    if not (folder / DATABASE).is_file() or related(folder, root) or folder in (Path.home(), Path('/')) or len(folder.parts) < 3:
        raise ValueError('The earlier copy is no longer there.')
    freed = measure(folder)['bytes']
    shutil.rmtree(folder)
    config = read_config()
    config.pop('previous', None)
    write_config(config)
    return dict(status(api), freed=freed)


def blocked(api):
    """Why ordinary requests cannot run now, or None."""
    if not getattr(api, 'STORAGE_AVAILABLE', True):
        name = volume(api.ROOT)[0]
        return f'CrateLoom’s library is on “{name}”, which is not connected. Connect it and choose Try again.'
    with MOVE_LOCK:
        state = MOVE.get('state')
    if state == 'running':
        return 'CrateLoom is moving its library. Wait until the move finishes.'
    if state in ('done', 'failed', 'cancelled'):
        return 'CrateLoom needs to restart its engine after the library move.'
    return None


def dispatch(api, method, p, stop_background):
    if method == 'storage.status':
        return status(api)
    default = p.get('default') is True
    if method == 'storage.check':
        return plan(api, p.get('path'), default)
    if method == 'storage.move':
        return start_move(api, p.get('path'), default, stop_background)
    if method == 'storage.use':
        return use_existing(api, p.get('path'), default)
    if method == 'storage.cancel':
        with MOVE_LOCK:
            if MOVE.get('state') == 'running':
                MOVE['cancel'].set()
        return status(api)
    if method == 'storage.discard_previous':
        if blocked(api):
            raise ValueError(blocked(api))
        return discard_previous(api)
    raise ValueError('Unknown storage method')
