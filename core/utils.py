import os
import json
import tempfile
import threading
from .constants import logger

_file_locks = {}
_lock_mutex = threading.Lock()

def get_file_lock(filepath):
    abs_path = os.path.abspath(filepath)
    with _lock_mutex:
        if abs_path not in _file_locks:
            _file_locks[abs_path] = threading.Lock()
        return _file_locks[abs_path]

def safe_load_json(filepath, default=None):
    if default is None:
        default = {}
    if not os.path.exists(filepath):
        return default
    
    with get_file_lock(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Error loading {filepath}: {e}")
            return default

def safe_save_json(filepath, data):
    dir_path = os.path.dirname(os.path.abspath(filepath)) or '.'
    filename = os.path.basename(filepath)
    
    with get_file_lock(filepath):
        fd, tmp = tempfile.mkstemp(dir=dir_path, prefix=f'.{filename}_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, filepath)
            return True
        except Exception as e:
            logger.error(f"Error saving {filepath}: {e}")
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return False
