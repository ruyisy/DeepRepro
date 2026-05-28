"""
Workflow Service - Integration with DeepRepro workflows

NOTE: This module uses lazy imports for workflow modules (workflows, mcp_agent).
sys.path is configured in main.py at startup. Background tasks share the same
sys.path, so workflow modules will be found correctly as long as there are
no naming conflicts (config.py -> settings.py, utils/ -> app_utils/).
"""

import asyncio
import uuid
import os
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field

from settings import CONFIG_PATH, PROJECT_ROOT


DEEPREPRO_EVENT_SCHEMAS: Dict[str, int] = {
    "stage": 1,
    "agent_state": 1,
    "round_start": 1,
    "file_progress": 1,
    "round_done": 1,
    "artifact": 1,
}


@dataclass
class WorkflowTask:
    """Represents a running workflow task"""

    task_id: str
    status: str = "pending"  # pending | running | waiting_for_input | completed | error | cancelled
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    # User-in-Loop support
    pending_interaction: Optional[Dict[str, Any]] = (
        None  # Current interaction request waiting for user
    )


@dataclass
class BatchWorkflowItem:
    """One paper-to-code task inside a batch"""

    task_id: str
    input_source: str
    input_type: str
    label: Optional[str]
    order: int


@dataclass
class BatchWorkflow:
    """A serial batch of paper-to-code workflow tasks"""

    batch_id: str
    items: List[BatchWorkflowItem]
    status: str = "pending"
    current_index: int = -1
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


class WorkflowService:
    """Service for managing workflow execution"""

    def __init__(self):
        self._tasks: Dict[str, WorkflowTask] = {}
        self._batches: Dict[str, BatchWorkflow] = {}
        self._workflow_lock = asyncio.Lock()
        # Changed: Each task can have multiple subscriber queues
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # User-in-Loop plugin integration (lazy loaded)
        self._plugin_integration = None
        self._plugin_enabled = True  # Can be disabled via config

    def _get_plugin_integration(self):
        """Lazy load the plugin integration system."""
        if self._plugin_integration is None and self._plugin_enabled:
            try:
                from workflows.plugins.integration import WorkflowPluginIntegration

                self._plugin_integration = WorkflowPluginIntegration(self)
                print("[WorkflowService] Plugin integration initialized")
            except ImportError as e:
                print(f"[WorkflowService] Plugin system not available: {e}")
                self._plugin_enabled = False
        return self._plugin_integration

    def create_task(self) -> WorkflowTask:
        """Create a new workflow task"""
        task_id = str(uuid.uuid4())
        task = WorkflowTask(task_id=task_id)
        self._tasks[task_id] = task
        self._subscribers[task_id] = []
        return task

    def get_task(self, task_id: str) -> Optional[WorkflowTask]:
        """Get task by ID"""
        return self._tasks.get(task_id)

    def create_paper_to_code_batch(
        self,
        items: List[Dict[str, Any]],
    ) -> BatchWorkflow:
        """Create a serial paper-to-code batch with one task per input item."""
        batch_id = str(uuid.uuid4())
        batch_items: List[BatchWorkflowItem] = []

        for index, item in enumerate(items):
            task = self.create_task()
            task.message = "Queued in batch"
            batch_items.append(
                BatchWorkflowItem(
                    task_id=task.task_id,
                    input_source=item["input_source"],
                    input_type=item["input_type"],
                    label=item.get("label"),
                    order=index,
                )
            )

        batch = BatchWorkflow(batch_id=batch_id, items=batch_items)
        self._batches[batch_id] = batch
        return batch

    def get_batch(self, batch_id: str) -> Optional[BatchWorkflow]:
        """Get batch by ID."""
        return self._batches.get(batch_id)

    def serialize_batch(self, batch: BatchWorkflow) -> Dict[str, Any]:
        """Serialize a batch and its task statuses."""
        return {
            "batch_id": batch.batch_id,
            "status": batch.status,
            "current_index": batch.current_index,
            "started_at": batch.started_at.isoformat() if batch.started_at else None,
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
            "tasks": [
                {
                    "task_id": item.task_id,
                    "input_source": item.input_source,
                    "input_type": item.input_type,
                    "label": item.label,
                    "order": item.order,
                    "status": self._tasks[item.task_id].status
                    if item.task_id in self._tasks
                    else "missing",
                    "progress": self._tasks[item.task_id].progress
                    if item.task_id in self._tasks
                    else 0,
                    "message": self._tasks[item.task_id].message
                    if item.task_id in self._tasks
                    else "",
                    "error": self._tasks[item.task_id].error
                    if item.task_id in self._tasks
                    else None,
                }
                for item in batch.items
            ],
        }

    def subscribe(self, task_id: str) -> Optional[asyncio.Queue]:
        """Subscribe to a task's progress updates. Returns a new queue for this subscriber."""
        if task_id not in self._subscribers:
            print(f"[Subscribe] Failed: task={task_id[:8]}... not found in subscribers")
            return None
        queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        print(
            f"[Subscribe] Success: task={task_id[:8]}... total_subscribers={len(self._subscribers[task_id])}"
        )
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        """Unsubscribe from a task's progress updates."""
        if task_id in self._subscribers and queue in self._subscribers[task_id]:
            self._subscribers[task_id].remove(queue)
            print(
                f"[Unsubscribe] task={task_id[:8]}... remaining={len(self._subscribers[task_id])}"
            )

    async def _broadcast(self, task_id: str, message: Dict[str, Any]):
        """Broadcast a message to all subscribers of a task."""
        if task_id in self._subscribers:
            subscriber_count = len(self._subscribers[task_id])
            print(
                f"[Broadcast] task={task_id[:8]}... type={message.get('type')} subscribers={subscriber_count}"
            )
            for queue in self._subscribers[task_id]:
                try:
                    await queue.put(message)
                except Exception as e:
                    print(f"[Broadcast] Failed to send to queue: {e}")
        else:
            print(
                f"[Broadcast] No subscribers for task={task_id[:8]}... type={message.get('type')}"
            )

    def _create_deeprepro_event_callback(
        self, task_id: str
    ) -> Callable[[str, Dict[str, Any]], None]:
        """Create a non-blocking DeepRepro telemetry callback for UI-only events."""
        def callback(event_type: str, payload: Dict[str, Any]):
            event_payload = payload if isinstance(payload, dict) else {"value": payload}
            timestamp = datetime.utcnow().isoformat()
            asyncio.create_task(
                self._broadcast(
                    task_id,
                    {
                        "type": "deeprepro_event",
                        "task_id": task_id,
                        "event": event_type,
                        "payload": event_payload,
                        "schema_version": DEEPREPRO_EVENT_SCHEMAS.get(event_type, 1),
                        "timestamp": timestamp,
                    },
                )
            )

        return callback

    def get_progress_queue(self, task_id: str) -> Optional[asyncio.Queue]:
        """Get progress queue for a task (deprecated, use subscribe instead)"""
        # For backwards compatibility, create a subscriber queue
        return self.subscribe(task_id)

    async def _create_progress_callback(
        self, task_id: str
    ) -> Callable[[int, str], None]:
        """Create a progress callback that broadcasts to all subscribers"""
        task = self._tasks.get(task_id)

        def callback(progress: int, message: str):
            if task:
                task.progress = progress
                task.message = message

            # Broadcast to all subscribers
            asyncio.create_task(
                self._broadcast(
                    task_id,
                    {
                        "type": "progress",
                        "task_id": task_id,
                        "progress": progress,
                        "message": message,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            )

        return callback

    async def execute_paper_to_code(
        self,
        task_id: str,
        input_source: str,
        input_type: str,
        enable_indexing: bool = False,
        workflow_mode: str = "raw_fast",
        supplementary_requirements: str = "",
        planning_image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute paper-to-code workflow"""
        async with self._workflow_lock:
            return await self._execute_paper_to_code_unlocked(
                task_id,
                input_source,
                input_type,
                enable_indexing,
                workflow_mode,
                supplementary_requirements,
                planning_image_paths,
            )

    async def _execute_paper_to_code_unlocked(
        self,
        task_id: str,
        input_source: str,
        input_type: str,
        enable_indexing: bool = False,
        workflow_mode: str = "raw_fast",
        supplementary_requirements: str = "",
        planning_image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute paper-to-code workflow without acquiring the serial workflow lock."""
        # Lazy imports - workflow modules found via sys.path set in main.py
        from mcp_agent.app import MCPApp
        from workflows.agent_orchestration_engine import (
            execute_multi_agent_research_pipeline,
        )

        task = self._tasks.get(task_id)
        if not task:
            return {"status": "error", "error": "Task not found"}

        original_cwd = os.getcwd()
        mode_to_settings = {
            "raw_fast": (False, "fast"),
            "infer_fast": (True, "fast"),
            "raw_deepplan": (False, "deepplan"),
            "infer_deepplan": (True, "deepplan"),
        }
        if workflow_mode in mode_to_settings:
            enable_indexing, implementation_mode = mode_to_settings[workflow_mode]
        else:
            implementation_mode = "deepplan" if "deepplan" in workflow_mode else "fast"
            if enable_indexing and implementation_mode == "deepplan":
                workflow_mode = "infer_deepplan"
            elif enable_indexing:
                workflow_mode = "infer_fast"
            elif implementation_mode == "deepplan":
                workflow_mode = "raw_deepplan"
            else:
                workflow_mode = "raw_fast"

        task.status = "running"
        task.message = f"Starting DeepRepro {workflow_mode} run"
        task.started_at = datetime.utcnow()

        try:
            progress_callback = await self._create_progress_callback(task_id)
            event_callback = self._create_deeprepro_event_callback(task_id)

            event_callback(
                "stage",
                {
                    "stage": "workspace",
                    "label": "Workspace",
                    "message": task.message,
                    "workflow_mode": workflow_mode,
                    "implementation_mode": implementation_mode,
                    "reference_indexing": enable_indexing,
                },
            )

            # Change to project root directory for MCP server paths to work correctly
            os.chdir(PROJECT_ROOT)

            # Create MCP app context with explicit config path
            app = MCPApp(name="paper_to_code", settings=str(CONFIG_PATH))

            async with app.run() as agent_app:
                logger = agent_app.logger
                context = agent_app.context

                # Add current working directory to filesystem server args
                context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])

                # Execute the pipeline
                result = await execute_multi_agent_research_pipeline(
                    input_source,
                    logger,
                    progress_callback,
                    enable_indexing=enable_indexing,
                    implementation_mode=implementation_mode,
                    supplementary_requirements=supplementary_requirements or "",
                    planning_image_paths=planning_image_paths or [],
                    event_callback=event_callback,
                )

                result_text = str(result)
                failed = (
                    "Code implementation failed:" in result_text
                    or "DeepRepro pipeline failed:" in result_text
                    or "\n❌" in result_text
                )
                task.status = "error" if failed else "completed"
                task.progress = 100
                task.result = {
                    "status": "error" if failed else "success",
                    "repo_result": result,
                }
                task.completed_at = datetime.utcnow()

                if failed:
                    task.error = result_text
                    await self._broadcast(
                        task_id,
                        {
                            "type": "error",
                            "task_id": task_id,
                            "error": result_text,
                        },
                    )
                else:
                    # Broadcast completion signal to all subscribers
                    await self._broadcast(
                        task_id,
                        {
                            "type": "complete",
                            "task_id": task_id,
                            "status": "success",
                            "result": task.result,
                        },
                    )
                # Give WebSocket handlers time to receive the completion message
                await asyncio.sleep(0.5)

                return task.result

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.utcnow()

            # Broadcast error signal to all subscribers
            await self._broadcast(
                task_id,
                {
                    "type": "error",
                    "task_id": task_id,
                    "error": str(e),
                },
            )

            return {"status": "error", "error": str(e)}

        finally:
            # Restore original working directory
            os.chdir(original_cwd)

    async def execute_paper_to_code_batch(
        self,
        batch_id: str,
        enable_indexing: bool = False,
        workflow_mode: str = "raw_fast",
        supplementary_requirements: str = "",
        planning_image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute paper-to-code tasks in a batch sequentially."""
        batch = self._batches.get(batch_id)
        if not batch:
            return {"status": "error", "error": "Batch not found"}

        async with self._workflow_lock:
            batch.status = "running"
            batch.started_at = datetime.utcnow()

            for index, item in enumerate(batch.items):
                if batch.cancel_event.is_set():
                    break

                batch.current_index = index
                task = self._tasks.get(item.task_id)
                if task and task.status == "cancelled":
                    continue

                await self._execute_paper_to_code_unlocked(
                    item.task_id,
                    item.input_source,
                    item.input_type,
                    enable_indexing,
                    workflow_mode,
                    supplementary_requirements,
                    planning_image_paths,
                )

            batch.status = "cancelled" if batch.cancel_event.is_set() else "completed"
            batch.completed_at = datetime.utcnow()
            return {
                "status": "cancelled" if batch.cancel_event.is_set() else "success",
                "batch_id": batch.batch_id,
                "tasks": [
                    {
                        "task_id": item.task_id,
                        "status": self._tasks[item.task_id].status
                        if item.task_id in self._tasks
                        else "missing",
                    }
                    for item in batch.items
                ],
            }

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task"""
        task = self._tasks.get(task_id)
        if task and task.status == "running":
            task.cancel_event.set()
            task.status = "cancelled"
            for batch in self._batches.values():
                if any(item.task_id == task_id for item in batch.items):
                    batch.cancel_event.set()
                    batch.status = "cancelled"
            return True
        return False

    def cleanup_task(self, task_id: str):
        """Clean up task resources"""
        if task_id in self._tasks:
            del self._tasks[task_id]
        if task_id in self._subscribers:
            del self._subscribers[task_id]

    def get_active_tasks(self) -> List[WorkflowTask]:
        """Get all tasks that are currently running"""
        return [task for task in self._tasks.values() if task.status == "running"]

    def get_recent_tasks(self, limit: int = 10) -> List[WorkflowTask]:
        """Get recent tasks sorted by start time (newest first)"""
        tasks = list(self._tasks.values())
        # Sort by started_at descending (newest first)
        tasks.sort(key=lambda t: t.started_at or datetime.min, reverse=True)
        return tasks[:limit]


# Global service instance
workflow_service = WorkflowService()
