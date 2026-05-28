"""DeepRepro workflow API routes."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from services.workflow_service import workflow_service
from models.requests import (
    PaperToCodeRequest,
    PaperToCodeBatchRequest,
    InteractionResponseRequest,
)
from models.responses import TaskResponse, BatchTaskResponse, BatchTaskItemResponse


router = APIRouter()


@router.post("/paper-to-code", response_model=TaskResponse)
async def start_paper_to_code(
    request: PaperToCodeRequest,
    background_tasks: BackgroundTasks,
):
    """
    Start a paper-to-code workflow.
    Returns a task ID that can be used to track progress via WebSocket.
    """
    task = workflow_service.create_task()

    # Run workflow in background
    background_tasks.add_task(
        workflow_service.execute_paper_to_code,
        task.task_id,
        request.input_source,
        request.input_type,
        request.enable_indexing,
        request.workflow_mode,
        request.supplementary_requirements,
        request.planning_image_paths,
    )

    return TaskResponse(
        task_id=task.task_id,
        status="started",
        message="DeepRepro paper-to-code run started",
    )


@router.post("/paper-to-code/batch", response_model=BatchTaskResponse)
async def start_paper_to_code_batch(
    request: PaperToCodeBatchRequest,
    background_tasks: BackgroundTasks,
):
    """
    Start a serial batch of paper-to-code workflows.
    Each paper gets its own task ID and runs in submitted order.
    """
    batch = workflow_service.create_paper_to_code_batch(
        [item.dict() for item in request.items]
    )

    background_tasks.add_task(
        workflow_service.execute_paper_to_code_batch,
        batch.batch_id,
        request.enable_indexing,
        request.workflow_mode,
        request.supplementary_requirements,
        request.planning_image_paths,
    )

    return BatchTaskResponse(
        batch_id=batch.batch_id,
        status="started",
        message="Batch DeepRepro paper-to-code run started",
        tasks=[
            BatchTaskItemResponse(
                task_id=item.task_id,
                input_source=item.input_source,
                input_type=item.input_type,
                label=item.label,
                order=item.order,
            )
            for item in batch.items
        ],
    )


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """Get the status of a batch workflow."""
    batch = workflow_service.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return workflow_service.serialize_batch(batch)


@router.get("/status/{task_id}")
async def get_workflow_status(task_id: str):
    """Get the status of a workflow task"""
    task = workflow_service.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    response = {
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "result": task.result,
        "error": task.error,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }

    # Include pending interaction if waiting for input
    if task.status == "waiting_for_input" and task.pending_interaction:
        response["pending_interaction"] = task.pending_interaction

    return response


@router.post("/cancel/{task_id}")
async def cancel_workflow(task_id: str):
    """Cancel a running workflow"""
    success = workflow_service.cancel_task(task_id)

    if not success:
        raise HTTPException(
            status_code=400,
            detail="Task not found or cannot be cancelled",
        )

    return {"status": "cancelled", "task_id": task_id}


@router.post("/respond/{task_id}")
async def respond_to_interaction(task_id: str, request: InteractionResponseRequest):
    """
    Submit user's response to a pending interaction.

    This is used for User-in-Loop functionality where the workflow
    pauses to ask the user for input (e.g., requirement questions,
    plan confirmation).
    """
    task = workflow_service.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != "waiting_for_input":
        raise HTTPException(
            status_code=400,
            detail=f"Task is not waiting for input (current status: {task.status})",
        )

    # Check if plugin integration is available
    if not hasattr(workflow_service, "_plugin_integration"):
        raise HTTPException(
            status_code=501, detail="User-in-Loop plugin system not enabled"
        )

    success = workflow_service._plugin_integration.submit_response(
        task_id=task_id,
        action=request.action,
        data=request.data,
        skipped=request.skipped,
    )

    if not success:
        raise HTTPException(
            status_code=400, detail="No pending interaction for this task"
        )

    return {
        "status": "ok",
        "task_id": task_id,
        "action": request.action,
    }


@router.get("/interaction/{task_id}")
async def get_pending_interaction(task_id: str):
    """
    Get the pending interaction for a task, if any.

    Returns the interaction data that needs user response.
    """
    task = workflow_service.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != "waiting_for_input" or not task.pending_interaction:
        return {
            "has_interaction": False,
            "task_id": task_id,
            "status": task.status,
        }

    return {
        "has_interaction": True,
        "task_id": task_id,
        "status": task.status,
        "interaction": task.pending_interaction,
    }


@router.get("/active")
async def get_active_tasks():
    """
    Get all active (running) tasks.
    Useful for recovering tasks after page refresh.
    """
    active_tasks = workflow_service.get_active_tasks()
    return {
        "tasks": [
            {
                "task_id": task.task_id,
                "status": task.status,
                "progress": task.progress,
                "message": task.message,
                "started_at": task.started_at,
            }
            for task in active_tasks
        ]
    }


@router.get("/recent")
async def get_recent_tasks(limit: int = 10):
    """
    Get recent tasks (completed, error, or running).
    Useful for task history.
    """
    recent_tasks = workflow_service.get_recent_tasks(limit)
    return {
        "tasks": [
            {
                "task_id": task.task_id,
                "status": task.status,
                "progress": task.progress,
                "message": task.message,
                "result": task.result,
                "error": task.error,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            }
            for task in recent_tasks
        ]
    }
