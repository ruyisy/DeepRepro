"""Small durable checkpoints; never persist live clients or credentials."""

import json
import os
import tempfile
from pathlib import Path


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def relative_code_path(path, code_directory):
    """Use one identity for absolute and relative paths inside the workspace."""
    root = Path(code_directory).resolve()
    candidate = Path(str(path).replace("\\", "/"))
    return (candidate if candidate.is_absolute() else root / candidate).resolve().relative_to(root).as_posix()
