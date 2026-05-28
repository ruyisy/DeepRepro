"""Centralized DeepRepro workspace path helpers."""

import os
import re
from typing import List


WORKSPACE_ROOT_NAME = "deeprepro_code"
TASK_PREFIX = "task"


def get_workspace_dir(base_dir: str | None = None) -> str:
    """Return the DeepRepro workspace directory."""
    root = base_dir or os.getcwd()
    return os.path.join(root, WORKSPACE_ROOT_NAME)


def get_task_dir(task_id: int, base_dir: str | None = None) -> str:
    """Return the directory for one DeepRepro reproduction task."""
    return os.path.join(get_workspace_dir(base_dir), f"{TASK_PREFIX}{task_id}")


def list_task_ids(workspace_dir: str) -> List[int]:
    """List numeric task ids from taskN directories."""
    if not os.path.isdir(workspace_dir):
        return []

    task_ids: List[int] = []
    pattern = re.compile(rf"^{re.escape(TASK_PREFIX)}(\d+)$")
    for name in os.listdir(workspace_dir):
        match = pattern.match(name)
        if match and os.path.isdir(os.path.join(workspace_dir, name)):
            task_ids.append(int(match.group(1)))
    return sorted(task_ids)


def next_task_id(workspace_dir: str) -> int:
    """Return the next available task id for the workspace."""
    existing_ids = list_task_ids(workspace_dir)
    return max(existing_ids) + 1 if existing_ids else 1
