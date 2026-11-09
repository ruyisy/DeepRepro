"""Download and preview only artifacts belonging to a known run."""

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services.workflow_service import workflow_service
from settings import PROJECT_ROOT
from utils.workspace_paths import get_workspace_dir

router = APIRouter()


def run_directory(task_id: str) -> Path:
    task = workflow_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    code_directory = task.artifacts.get("code_directory")
    if not code_directory:
        raise HTTPException(404, "No artifacts available yet")
    root = Path(get_workspace_dir(str(PROJECT_ROOT)))
    root = (PROJECT_ROOT / root).resolve()
    directory = (PROJECT_ROOT / code_directory).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir():
        raise HTTPException(404, "Artifact workspace not found")
    return directory


def artifact_files(directory: Path):
    return sorted(p for p in directory.rglob("*") if p.is_file()
                  and not p.is_symlink() and p.resolve().is_relative_to(directory)
                  and not any(part.startswith(".") or part in {"__pycache__", "node_modules"}
                              for part in p.relative_to(directory).parts))


@router.get("/{task_id}")
async def list_artifacts(task_id: str):
    directory = run_directory(task_id)
    return {"files": [str(p.relative_to(directory)) for p in artifact_files(directory)]}


@router.get("/{task_id}/file")
async def preview_artifact(task_id: str, path: str):
    directory = run_directory(task_id)
    target = (directory / path).resolve()
    if not target.is_relative_to(directory) or target not in artifact_files(directory):
        raise HTTPException(404, "Artifact not found")
    if target.stat().st_size > 1024 * 1024:
        raise HTTPException(413, "Preview is limited to 1 MB; download the workspace instead")
    try:
        return {"path": path, "content": target.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(415, "Binary files are available in the workspace download")


@router.get("/{task_id}/download")
async def download_artifacts(task_id: str):
    directory = run_directory(task_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in artifact_files(directory):
            archive.write(path, arcname=str(path.relative_to(directory)))
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="deeprepro-{task_id[:8]}.zip"'
    })


@router.get("/{task_id}/metrics")
async def download_metrics(task_id: str):
    task = workflow_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    import json
    return Response(json.dumps(workflow_service.metrics_snapshot(task), indent=2),
                    media_type="application/json", headers={
                        "Content-Disposition": f'attachment; filename="metrics-{task_id[:8]}.json"'
                    })
