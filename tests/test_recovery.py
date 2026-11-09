import asyncio
import json
from types import SimpleNamespace

import pytest


def test_artifact_lookup_is_independent_of_server_working_directory(tmp_path, monkeypatch):
    from api.routes import artifacts
    directory = tmp_path / "deeprepro_code" / "task1" / "generate_code"
    directory.mkdir(parents=True)
    backend = tmp_path / "ui" / "backend"
    backend.mkdir(parents=True)
    monkeypatch.chdir(backend)
    monkeypatch.setattr(artifacts, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(artifacts.workflow_service, "get_task", lambda _: SimpleNamespace(artifacts={"code_directory": str(directory)}))
    assert artifacts.run_directory("task") == directory


def test_stop_launcher_rejects_reused_pid(tmp_path, monkeypatch):
    import deeprepro
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"launcher_pid": 123, "identity": "old process"}))
    monkeypatch.setattr(deeprepro, "RUNTIME_FILE", runtime)
    monkeypatch.setattr(deeprepro, "process_identity", lambda _: "different process")
    signals = []
    monkeypatch.setattr(deeprepro.os, "kill", lambda *args: signals.append(args))
    deeprepro.stop_local()
    assert signals == []


def test_file_checkpoint_normalizes_paths_and_rejects_changed_content(tmp_path):
    from workflows.agents.memory_agent_concise import ConciseMemoryAgent
    from workflows.agents.code_implementation_agent import CodeImplementationAgent
    directory = tmp_path / "generate_code"
    directory.mkdir()
    (directory / "demo.py").write_text("value = 1")
    (directory / "nested").mkdir()
    (directory / "nested" / "demo.py").touch()
    memory = ConciseMemoryAgent("demo.py", target_directory=str(tmp_path), code_directory=str(directory))
    memory.current_round = 2
    agent = CodeImplementationAgent(None)
    agent.memory_agent = memory
    call = {"input": {"file_path": str(directory / "demo.py")}}
    assert not agent._track_file_implementation(call, '{"status":"error"}')
    assert agent.files_implemented_count == 0
    for name in [str(directory / "demo.py"), "demo.py"]:
        assert agent._track_file_implementation(call, {"status": "success", "file_path": name})
    assert agent.files_implemented_count == 1
    assert memory.get_all_files_list() == ["demo.py", "nested/demo.py"]
    restored = ConciseMemoryAgent("demo.py", target_directory=str(tmp_path), code_directory=str(directory))
    assert restored.current_round == 2
    assert restored.get_implemented_files() == ["demo.py"]
    assert restored.get_unimplemented_files() == ["nested/demo.py"]
    (directory / "demo.py").write_text("partially changed")
    changed = ConciseMemoryAgent("demo.py", target_directory=str(tmp_path), code_directory=str(directory))
    assert changed.get_implemented_files() == []


@pytest.mark.asyncio
async def test_pause_restart_resume_and_retry_keep_checkpoint_and_metrics(tmp_path, monkeypatch):
    import mcp_agent.app
    from workflows import agent_orchestration_engine as engine
    from services import workflow_service as module
    from utils.run_metrics import current_metrics
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    entered = asyncio.Event()
    calls = []
    async def pipeline(*args, checkpoint, checkpoint_callback, **kwargs):
        calls.append(dict(checkpoint))
        current_metrics.get().record("test", "planning", 0.01, {"total_tokens": 5})
        if not checkpoint:
            checkpoint.update({"stages": {"planning": "saved-plan"}})
            checkpoint_callback(checkpoint)
            entered.set()
            await asyncio.Event().wait()
        if len(calls) == 2:
            raise ConnectionError("temporary model outage")
        return "Code implementation completed successfully"
    class FakeApp:
        def __init__(self, **kwargs):
            self.logger = None
            self.context = SimpleNamespace(config=SimpleNamespace(mcp=SimpleNamespace(servers={"filesystem": SimpleNamespace(command="python", args=[])})))
        def run(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    monkeypatch.setattr(mcp_agent.app, "MCPApp", FakeApp)
    monkeypatch.setattr(engine, "execute_multi_agent_research_pipeline", pipeline)
    service = module.WorkflowService()
    task = service.create_task()
    service.prepare_task(task, {"input_source": "paper.md", "input_type": "file"})
    service.launch_task(task)
    await asyncio.wait_for(entered.wait(), 2)
    # Durable while still running, even before normal task completion.
    crashed = module.WorkflowService().get_task(task.task_id)
    assert crashed.status == "paused"
    assert crashed.checkpoint["stages"]["planning"] == "saved-plan"
    runner = task.runner
    assert service.cancel_task(task.task_id, pause=True)
    with pytest.raises(ValueError):
        service.resume_task(task.task_id)  # Must not race unfinished cleanup.
    await asyncio.wait_for(runner, 2)
    assert task.status == "paused" and not service._workflow_lock.locked()
    service = module.WorkflowService()
    resumed = service.resume_task(task.task_id)
    await asyncio.wait_for(resumed.runner, 2)
    assert resumed.status == "error"
    retried = service.resume_task(task.task_id)
    await asyncio.wait_for(retried.runner, 2)
    assert retried.status == "completed" and retried.attempts == 3
    assert calls[1] == calls[2] == {"stages": {"planning": "saved-plan"}}
    assert service.metrics_snapshot(retried)["total_tokens"] == 15
    assert json.loads((tmp_path / "logs" / "runs" / f"{task.task_id}.json").read_text())["status"] == "completed"


@pytest.mark.asyncio
async def test_pipeline_reuses_saved_stages_after_interruption(tmp_path, monkeypatch):
    from workflows import agent_orchestration_engine as engine
    logger = SimpleNamespace(info=lambda *a, **k: None)
    checkpoint, calls = {}, []
    monkeypatch.setattr(engine, "get_workspace_dir", lambda: str(tmp_path))
    async def process(value, logger): calls.append("input"); return value
    async def research(*args): calls.append("research"); return "analysis", "download"
    async def workspace(*args):
        calls.append("workspace")
        return {"paper_dir": str(tmp_path), "initial_plan_path": str(tmp_path / "plan.txt"),
                "reference_path": str(tmp_path / "ref.txt"), "download_path": str(tmp_path / "download.txt"),
                "index_report_path": str(tmp_path / "index.txt")}
    async def preprocess(info, logger):
        calls.append("preprocess"); info["use_segmentation"] = False
        return {"status": "success"}
    async def planning(info, *args):
        calls.append("planning"); assert info["use_segmentation"] is False
        (tmp_path / "plan.txt").write_text("saved plan")
    async def implementation(*args):
        calls.append("implementation")
        if calls.count("implementation") == 1:
            raise asyncio.CancelledError()
        return {"status": "success", "code_directory": str(tmp_path / "generate_code")}
    for name, value in {"_process_input_source": process, "orchestrate_research_analysis_agent": research,
        "synthesize_workspace_infrastructure_agent": workspace, "orchestrate_document_preprocessing_agent": preprocess,
        "orchestrate_code_planning_agent": planning, "synthesize_code_implementation_agent": implementation}.items():
        monkeypatch.setattr(engine, name, value)
    with pytest.raises(asyncio.CancelledError):
        await engine.execute_multi_agent_research_pipeline("paper.md", logger, enable_indexing=False, checkpoint=checkpoint)
    restored = json.loads(json.dumps(checkpoint))
    result = await engine.execute_multi_agent_research_pipeline("paper.md", logger, enable_indexing=False, checkpoint=restored)
    assert "completed successfully" in result
    assert calls == ["input", "research", "workspace", "preprocess", "planning", "implementation", "implementation"]
