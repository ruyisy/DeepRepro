import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from utils.run_metrics import RunMetrics, current_metrics
from utils.openai_client import AsyncOpenAI


@pytest.mark.asyncio
async def test_settings_blank_key_keeps_saved_credential(tmp_path, monkeypatch):
    from api.routes import config
    from models.requests import LLMConfigUpdateRequest
    config_path, secrets_path = tmp_path / "config.yaml", tmp_path / "secrets.yaml"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config, "SECRETS_PATH", secrets_path)
    monkeypatch.setattr(config, "load_mcp_config", lambda: {"openai": {}})
    monkeypatch.setattr(config, "load_secrets", lambda: {"openai": {"api_key": "test-saved-key"}})
    await config.set_llm_config(LLMConfigUpdateRequest(provider="openai", default_model="deepseek-flash"))
    assert yaml.safe_load(secrets_path.read_text())["openai"]["api_key"] == "test-saved-key"
    assert secrets_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_deepseek_adapter_records_real_usage_and_request_mode():
    seen = []
    async def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15, "prompt_cache_hit_tokens": 8},
        })
    metrics = RunMetrics()
    token = current_metrics.set(metrics)
    try:
        async with AsyncOpenAI(api_key="test-only", base_url="https://api.deepseek.com", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
            await client.chat.completions.create(model="deepseek-flash", messages=[{"role": "user", "content": "test"}])
    finally:
        current_metrics.reset(token)
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert metrics.snapshot()["total_tokens"] == 15
    assert metrics.snapshot()["cached_tokens"] == 8
    assert metrics.snapshot()["usage_reported_calls"] == 1
    assert "test-only" not in json.dumps(metrics.requests)


@pytest.mark.asyncio
async def test_plain_text_input_is_not_renamed_to_pdf(tmp_path, monkeypatch):
    from workflows import agent_orchestration_engine as engine
    source = tmp_path / "input.md"
    source.write_text("# Paper\nImportant formula: x = 1", encoding="utf-8")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(engine, "get_workspace_dir", lambda: str(workspace))
    monkeypatch.setattr(engine, "get_task_dir", lambda _: str(workspace / "task1"))
    logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)
    result = json.loads(await engine.run_resource_processor(json.dumps({"input_type": "file", "path": str(source)}), logger))
    assert result["status"] == "success"
    assert (workspace / "task1" / "1.md").read_text() == source.read_text()
    assert source.exists()


@pytest.mark.asyncio
async def test_cancel_stops_pipeline_and_preserves_terminal_status(tmp_path, monkeypatch):
    import mcp_agent.app
    from workflows import agent_orchestration_engine as engine
    from services import workflow_service as module
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    started, stopped = asyncio.Event(), asyncio.Event()
    async def pipeline(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
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
    running = asyncio.create_task(service.execute_paper_to_code(task.task_id, "paper.md", "file"))
    await asyncio.wait_for(started.wait(), 2)
    assert service.cancel_task(task.task_id)
    await asyncio.wait_for(running, 2)
    assert stopped.is_set()
    assert task.status == "cancelled"
    assert task.runner is None
    assert not service._workflow_lock.locked()
    restored = module.WorkflowService().get_task(task.task_id)
    assert restored.status == "cancelled"


def test_artifact_preview_blocks_workspace_escape(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from api.routes import artifacts
    directory = tmp_path / "workspace"
    directory.mkdir()
    (directory / "demo.py").write_text("print('ok')")
    (tmp_path / "secret.txt").write_text("private")
    (directory / "link.txt").symlink_to(tmp_path / "secret.txt")
    monkeypatch.setattr(artifacts, "run_directory", lambda _: directory)
    with TestClient(app) as client:
        assert client.get('/api/v1/artifacts/test/file', params={'path': '../secret.txt'}).status_code == 404
        assert client.get('/api/v1/artifacts/test/file', params={'path': 'link.txt'}).status_code == 404
        assert client.get('/api/v1/artifacts/test/file', params={'path': 'demo.py'}).json()['content'] == "print('ok')"
        assert client.get('/api/v1/artifacts/test').json()['files'] == ['demo.py']


@pytest.mark.asyncio
async def test_background_summaries_are_cancelled():
    from workflows.agents.code_implementation_agent import CodeImplementationAgent
    agent = object.__new__(CodeImplementationAgent)
    stopped = asyncio.Event()
    async def summary():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(summary())
    agent.pending_summary_tasks = [task]
    await asyncio.sleep(0)
    await agent.cancel_pending_summaries()
    assert stopped.is_set() and task.cancelled()
    agent._discard_summary_task(task)  # CancelledError must not leak from callbacks.
    assert not agent.pending_summary_tasks


@pytest.mark.asyncio
async def test_blocking_quality_findings_cannot_be_success(tmp_path, monkeypatch):
    from workflows.code_implementation_workflow import CodeImplementationWorkflow
    workflow = CodeImplementationWorkflow()
    monkeypatch.setattr(workflow, "_read_plan_file", lambda _: "plan")
    monkeypatch.setattr(workflow, "_check_file_tree_exists", lambda _: True)
    async def implementation(*args):
        workflow.latest_quality_findings = [{"severity": "blocking", "message": "bad import"}]
        return "report"
    monkeypatch.setattr(workflow, "implement_code_pure", implementation)
    result = await workflow.run_workflow("plan.txt", str(tmp_path), pure_code_mode=True)
    assert result["status"] == "error"
    assert "blocking" in result["message"]
