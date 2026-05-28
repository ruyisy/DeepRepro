import asyncio
import importlib.util
import json
import logging
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, relative_path: str):
    module_path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CodeImplementationAgent = load_module(
    "paper_to_code_v1_code_agent_test_module",
    "workflows/agents/code_implementation_agent.py",
).CodeImplementationAgent
ConciseMemoryAgent = load_module(
    "paper_to_code_v1_memory_agent_test_module",
    "workflows/agents/memory_agent_concise.py",
).ConciseMemoryAgent
CodeImplementationWorkflow = load_module(
    "paper_to_code_v1_standard_workflow_test_module",
    "workflows/code_implementation_workflow.py",
).CodeImplementationWorkflow
CodeImplementationWorkflowWithIndex = load_module(
    "paper_to_code_v1_index_workflow_test_module",
    "workflows/code_implementation_workflow_index.py",
).CodeImplementationWorkflowWithIndex
orchestration_module = load_module(
    "paper_to_code_v1_orchestration_test_module",
    "workflows/agent_orchestration_engine.py",
)
prompts_module = load_module(
    "paper_to_code_v1_prompts_test_module",
    "prompts/code_prompts.py",
)


class FakeMCPAgent:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, tool_input):
        self.calls.append((tool_name, dict(tool_input)))
        if tool_name == "write_file":
            return json.dumps(
                {
                    "status": "success",
                    "file_path": tool_input.get("file_path", ""),
                },
                ensure_ascii=False,
            )
        return json.dumps({"status": "success"}, ensure_ascii=False)


class FakeMCPHistoryAgent:
    async def call_tool(self, tool_name, tool_input):
        self.tool_name = tool_name
        self.tool_input = dict(tool_input)
        return FakeCallToolResult(
            json.dumps(
                {
                    "total_operations": 3,
                    "history": [
                        {
                            "action": "write_file",
                            "details": {"file_path": "pkg/a.py"},
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )


class FakeMCPHistoryAgentDuplicateWrites:
    async def call_tool(self, tool_name, tool_input):
        self.tool_name = tool_name
        self.tool_input = dict(tool_input)
        return FakeCallToolResult(
            json.dumps(
                {
                    "total_operations": 4,
                    "history": [
                        {
                            "action": "write_file",
                            "details": {"file_path": "pkg/a.py"},
                        },
                        {
                            "action": "write_file",
                            "details": {"file_path": "pkg/a.py"},
                        },
                        {
                            "action": "write_file",
                            "details": {"file_path": "pkg/b.py"},
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )


class FakeCallToolContent:
    def __init__(self, text):
        self.text = text


class FakeCallToolResult:
    def __init__(self, text):
        self.content = [FakeCallToolContent(text)]


class StrictLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, name=None, context=None, **data):
        if context is not None:
            _ = context.session_id
        self.messages.append(("info", message))

    def warning(self, message, name=None, context=None, **data):
        if context is not None:
            _ = context.session_id
        self.messages.append(("warning", message))

    def error(self, message, name=None, context=None, **data):
        if context is not None:
            _ = context.session_id
        self.messages.append(("error", message))


class FakeAgent:
    def __init__(self, name, instruction, server_names):
        self.name = name

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def attach_llm(self, _llm_class):
        agent_name = self.name

        class _FakeLLM:
            async def generate_str(self, message, request_params=None):
                if agent_name == "CodePlannerAgent":
                    return "planner-output"
                return f"{agent_name}-output"

        return _FakeLLM()


class RecordingAgent(FakeAgent):
    instances = []

    def __init__(self, name, instruction, server_names):
        super().__init__(name, instruction, server_names)
        self.server_names = list(server_names)
        RecordingAgent.instances.append(self)


class FakeRequirementAnalysisAgent:
    def __init__(self, logger=None):
        self.logger = logger

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeWorkflowCodeAgent:
    def __init__(self, mcp_agent=None, logger=None, enable_read_tools=True):
        self.mcp_agent = mcp_agent
        self.logger = logger
        self.enable_read_tools = enable_read_tools
        self._summary = {"completed_files": [{"file": "pkg/a.py"}]}
        self._system_prompt = "system-prompt"

    def set_memory_agent(self, memory_agent, client, client_type):
        self.memory_agent = memory_agent
        self.client = client
        self.client_type = client_type

    def set_system_prompt(self, system_prompt):
        self._system_prompt = system_prompt

    def get_system_prompt(self):
        return self._system_prompt

    async def execute_tool_calls(self, tool_calls):
        return [{"result": "ok"} for _ in tool_calls]

    def get_files_implemented_count(self):
        return 1

    def get_implementation_summary(self):
        return self._summary


class FakeWorkflowMemoryAgent:
    instances = []

    def __init__(self, *args, **kwargs):
        self.rounds = []
        self.recorded_tool_results = []
        self.recorded_files = []
        FakeWorkflowMemoryAgent.instances.append(self)

    def start_new_round(self, iteration):
        self.rounds.append(iteration)

    def format_subplan_for_execution(self, subplan):
        return f"Target File: {subplan.get('target_file', '')}"

    def format_round_summaries_for_execution(self, summary_records):
        return "\n".join(
            f"- {record.get('file', '')}: {', '.join(record.get('exports', []) or [])}"
            for record in summary_records
        ) or "[]"

    def record_tool_result(self, **kwargs):
        self.recorded_tool_results.append(kwargs)

    def should_trigger_memory_optimization(self, messages, files_implemented_count):
        return False

    def apply_memory_optimization(
        self, current_system_message, messages, files_implemented_count
    ):
        return messages

    def record_file_implementation(self, file_path):
        self.recorded_files.append(file_path)

    def get_unimplemented_files(self):
        return []


class FakeInitAgent:
    instances = []

    def __init__(self, name, instruction, server_names):
        self.name = name
        self.instruction = instruction
        self.server_names = list(server_names)
        self.tool_calls = []
        FakeInitAgent.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def attach_llm(self, _llm_class):
        return object()

    async def call_tool(self, tool_name, tool_input):
        self.tool_calls.append((tool_name, dict(tool_input)))
        return {"status": "success"}


class FakeStatsCodeAgent:
    def get_implementation_statistics(self):
        return {
            "total_files_implemented": 1,
            "read_tools_status": {
                "read_tools_enabled": True,
                "status": "enabled",
                "tools_affected": ["read_file", "read_code_mem"],
            },
            "files_implemented_count": 1,
            "technical_decisions_count": 0,
            "constraints_count": 0,
            "architecture_notes_count": 0,
            "dependency_analysis_count": 0,
            "files_read_for_dependencies": 0,
            "last_summary_file_count": 0,
        }


class FakeStatsCodeAgentManyWrites:
    def get_implementation_statistics(self):
        return {
            "total_files_implemented": 36,
            "read_tools_status": {
                "read_tools_enabled": True,
                "status": "enabled",
                "tools_affected": ["read_file", "read_code_mem"],
            },
            "files_implemented_count": 36,
            "technical_decisions_count": 0,
            "constraints_count": 0,
            "architecture_notes_count": 0,
            "dependency_analysis_count": 0,
            "files_read_for_dependencies": 0,
            "last_summary_file_count": 0,
        }


class FakeStatsMemoryAgent:
    def get_memory_statistics(self, _files_implemented_count):
        return {
            "last_write_file_detected": True,
            "should_clear_memory_next": False,
            "implemented_files_tracked": 1,
            "current_round": 1,
            "concise_mode_active": True,
            "current_round_tool_results": 1,
            "essential_tools_recorded": 1,
        }


class DeterministicInputAnalysisTests(unittest.TestCase):
    def test_build_deterministic_input_analysis_for_local_pdf(self):
        pdf_path = str((ROOT / "deepcode_lab" / "test.pdf").resolve())
        result = orchestration_module._build_deterministic_input_analysis(pdf_path)
        parsed = json.loads(result)

        self.assertEqual(parsed["input_type"], "file")
        self.assertEqual(parsed["path"], pdf_path)

    def test_build_deterministic_input_analysis_for_https_url(self):
        result = orchestration_module._build_deterministic_input_analysis(
            "https://example.com/paper.pdf"
        )
        parsed = json.loads(result)

        self.assertEqual(parsed["input_type"], "url")
        self.assertEqual(parsed["path"], "https://example.com/paper.pdf")

    def test_run_research_analyzer_skips_agent_for_direct_file_input(self):
        pdf_path = str((ROOT / "deepcode_lab" / "test.pdf").resolve())

        with mock.patch.object(orchestration_module, "Agent") as mocked_agent:
            result = asyncio.run(
                orchestration_module.run_research_analyzer(pdf_path, logger=mock.Mock())
            )

        parsed = json.loads(result)
        self.assertEqual(parsed["input_type"], "file")
        self.assertEqual(parsed["path"], pdf_path)
        mocked_agent.assert_not_called()

    def test_initial_plan_content_validation_rejects_empty_content(self):
        self.assertFalse(orchestration_module._is_initial_plan_content_valid(""))

    def test_initial_plan_content_validation_accepts_structured_plan(self):
        plan = """
file_structure:
  - pkg/a.py
  - pkg/b.py
implementation_components:
  - name: core
    responsibility: implement the core algorithm and shared utilities
  - name: runner
    responsibility: wire the training and evaluation entrypoints
validation_approach:
  - smoke test
  - import validation
environment_setup:
  - python
  - torch
implementation_strategy:
  - build core module first
  - connect the runner after core exports are stable
  - validate interfaces before execution
"""
        with mock.patch.object(
            orchestration_module,
            "_assess_output_completeness",
            return_value=0.8,
        ):
            self.assertTrue(orchestration_module._is_initial_plan_content_valid(plan))


class FakeRetryMemoryAgent:
    def __init__(self):
        self.saved_subplan = None
        self.requested_target = None

    def get_progress_state(self):
        return {
            "pending_files": ["pkg/a.py", "pkg/b.py"],
            "completed_files": ["pkg/a.py"],
            "retry_target": "pkg/b.py",
            "retry_reason": "write_file Python syntax validation failed at line 1, column 8: invalid syntax",
        }

    def get_relevant_round_summaries(self, target_file=None, max_records=4):
        self.requested_target = target_file
        return [{"round": 2, "file": "pkg/a.py"}]

    def save_subplan(self, subplan):
        self.saved_subplan = subplan
        return f"subplans/round_{int(subplan['round']):03d}.json"


class FakeWorkflowLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class PaperToCodeV1RegressionTests(unittest.TestCase):
    def test_plan_file_extraction_ignores_natural_language_lines(self):
        plan_content = """# Minimal Reproduction Plan

## File Structure
generate_code/
  pkg/
    a.py
    b.py

## Implementation Components

### Phase 1: Core utilities
- `pkg/a.py`
  - Implement a pure function `add_one(value: int) -> int`

### Phase 2: Integration module
- `pkg/b.py`
  - Import `add_one` from `pkg.a`

## Implementation Strategy
- Implement `pkg/a.py` first
- Then implement `pkg/b.py` using the exported function from `pkg/a.py`
- Stop when both files are written
"""

        memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
        memory_agent.logger = logging.getLogger("paper_to_code_v1_test")
        memory_agent.initial_plan = plan_content

        self.assertEqual(
            memory_agent._extract_all_files_from_plan(),
            ["pkg/a.py", "pkg/b.py"],
        )

    def test_execute_tool_calls_normalizes_workspace_relative_paths(self):
        fake_mcp_agent = FakeMCPAgent()
        code_agent = CodeImplementationAgent(
            mcp_agent=fake_mcp_agent,
            enable_read_tools=True,
        )

        asyncio.run(
            code_agent.execute_tool_calls(
                [
                    {
                        "id": "write-1",
                        "name": "write_file",
                        "input": {
                            "file_path": "generate_code/pkg/a.py",
                            "content": "print('ok')",
                        },
                    },
                    {
                        "id": "search-1",
                        "name": "search_code_references",
                        "input": {
                            "indexes_path": "indexes",
                            "target_file": "./generate_code/pkg/a.py",
                        },
                    },
                ]
            )
        )

        self.assertEqual(fake_mcp_agent.calls[0][0], "write_file")
        self.assertEqual(fake_mcp_agent.calls[0][1]["file_path"], "pkg/a.py")
        self.assertEqual(fake_mcp_agent.calls[1][0], "search_code_references")
        self.assertEqual(fake_mcp_agent.calls[1][1]["target_file"], "pkg/a.py")

    def test_memory_agent_tracks_only_supported_round_tools(self):
        memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
        memory_agent.current_round_tool_results = []
        memory_agent.last_write_file_detected = False
        memory_agent.should_clear_memory_next = False

        memory_agent.record_tool_result(
            "execute_python",
            {"code": "print('ignored')"},
            {"status": "success"},
        )
        memory_agent.record_tool_result(
            "write_file",
            {"file_path": "pkg/a.py", "content": "print('ok')"},
            {"status": "success"},
        )

        self.assertTrue(memory_agent.last_write_file_detected)
        self.assertTrue(memory_agent.should_clear_memory_next)
        self.assertEqual(
            [record["tool_name"] for record in memory_agent.current_round_tool_results],
            ["write_file"],
        )

    def test_execution_prompts_do_not_reference_unavailable_execution_tools(self):
        prompt_texts = [
            prompts_module.PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
            prompts_module.PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT_INDEX,
            prompts_module.GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
        ]

        for prompt_text in prompt_texts:
            lowered = prompt_text.lower()
            self.assertNotIn("execute_python", lowered)
            self.assertNotIn("execute_bash", lowered)
            self.assertNotIn("bash and python tools", lowered)

    def test_v2_execution_prompts_require_batch_completion(self):
        prompt_texts = [
            prompts_module.PURE_CODE_IMPLEMENTATION_SYSTEM_PROMPT_INDEX,
            prompts_module.GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
        ]

        for prompt_text in prompt_texts:
            lowered = prompt_text.lower()
            self.assertIn("target_files", lowered)
            self.assertIn("file_order", lowered)
            self.assertIn("batch", lowered)
            self.assertNotIn("single function call per message", lowered)
            self.assertNotIn("one component at a time", lowered)

    def test_execute_tool_calls_skips_empty_write_file_content(self):
        fake_mcp_agent = FakeMCPAgent()
        code_agent = CodeImplementationAgent(
            mcp_agent=fake_mcp_agent,
            enable_read_tools=True,
        )

        results = asyncio.run(
            code_agent.execute_tool_calls(
                [
                    {
                        "id": "write-empty",
                        "name": "write_file",
                        "input": {
                            "file_path": "generate_code/pkg/empty.py",
                            "content": "",
                        },
                    }
                ]
            )
        )

        self.assertEqual(fake_mcp_agent.calls, [])
        self.assertEqual(code_agent.get_files_implemented_count(), 0)
        self.assertEqual(len(code_agent.get_implementation_summary()["completed_files"]), 0)
        self.assertEqual(
            json.loads(results[0]["result"])["message"],
            "write_file requires non-empty content",
        )

    def test_execute_tool_calls_skips_python_syntax_error_write(self):
        fake_mcp_agent = FakeMCPAgent()
        code_agent = CodeImplementationAgent(
            mcp_agent=fake_mcp_agent,
            enable_read_tools=True,
        )

        results = asyncio.run(
            code_agent.execute_tool_calls(
                [
                    {
                        "id": "write-bad-python",
                        "name": "write_file",
                        "input": {
                            "file_path": "generate_code/pkg/bad.py",
                            "content": "class Broken(:\n    pass\n",
                        },
                    }
                ]
            )
        )

        result_payload = json.loads(results[0]["result"])
        self.assertEqual(fake_mcp_agent.calls, [])
        self.assertEqual(code_agent.get_files_implemented_count(), 0)
        self.assertEqual(result_payload["status"], "error")
        self.assertEqual(result_payload["validation"], "python_syntax")
        self.assertIn("write_file Python syntax validation failed", result_payload["message"])

    def test_execute_tool_calls_skips_python_undefined_name_write(self):
        fake_mcp_agent = FakeMCPAgent()
        code_agent = CodeImplementationAgent(
            mcp_agent=fake_mcp_agent,
            enable_read_tools=True,
        )

        results = asyncio.run(
            code_agent.execute_tool_calls(
                [
                    {
                        "id": "write-bad-name",
                        "name": "write_file",
                        "input": {
                            "file_path": "generate_code/pkg/bad_name.py",
                            "content": (
                                "from dataclasses import dataclass\n\n"
                                "@dataclass\n"
                                "class ReasoningStructure:\n"
                                "    value: str = ''\n\n"
                                "def build() -> ReasonizationStructure:\n"
                                "    return ReasonizationStructure()\n"
                            ),
                        },
                    }
                ]
            )
        )

        result_payload = json.loads(results[0]["result"])
        self.assertEqual(fake_mcp_agent.calls, [])
        self.assertEqual(code_agent.get_files_implemented_count(), 0)
        self.assertEqual(result_payload["status"], "error")
        self.assertEqual(result_payload["validation"], "python_static")
        self.assertIn("undefined name", result_payload["message"].lower())

    def test_truncated_write_file_json_returns_none_in_both_workflows(self):
        standard_workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        index_workflow = CodeImplementationWorkflowWithIndex.__new__(
            CodeImplementationWorkflowWithIndex
        )
        malformed_json = '{"file_path":"pkg/a.py","content":"unterminated'

        self.assertIsNone(
            standard_workflow._repair_truncated_json(malformed_json, "write_file")
        )
        self.assertIsNone(
            index_workflow._repair_truncated_json(malformed_json, "write_file")
        )

    def test_subplan_round_is_forced_to_current_iteration(self):
        summary_records = [{"round": 3, "file": "pkg/helper.py"}]
        llm_output = json.dumps(
            {
                "round": 99,
                "target_file": "pkg/a.py",
                "goal": "implement a",
                "why_now": "next file",
                "must_implement": ["write a"],
                "summary_context_rounds": [3, 77],
            },
            ensure_ascii=False,
        )

        standard_workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        standard_workflow.logger = logging.getLogger("paper_to_code_v1_standard_round_test")
        standard_subplan = standard_workflow._parse_subplan_response(
            llm_output,
            iteration=5,
            pending_files=["pkg/a.py", "pkg/b.py"],
            summary_records=summary_records,
        )

        index_workflow = CodeImplementationWorkflowWithIndex.__new__(
            CodeImplementationWorkflowWithIndex
        )
        index_workflow.logger = logging.getLogger("paper_to_code_v1_index_round_test")
        index_subplan = index_workflow._parse_subplan_response(
            llm_output,
            iteration=5,
            pending_files=["pkg/a.py", "pkg/b.py"],
            summary_records=summary_records,
        )

        self.assertEqual(standard_subplan["round"], 5)
        self.assertEqual(index_subplan["round"], 5)
        self.assertEqual(standard_subplan["summary_context_rounds"], [3])
        self.assertEqual(index_subplan["summary_context_rounds"], [3])

    def test_generate_round_subplan_retries_failed_target_before_new_file(self):
        async def should_not_call_llm(*args, **kwargs):
            raise AssertionError("planner LLM should not be called for retry subplan")

        standard_workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        standard_workflow.logger = logging.getLogger("paper_to_code_v1_standard_retry_test")
        standard_workflow._call_llm_with_tools = should_not_call_llm
        standard_memory = FakeRetryMemoryAgent()
        standard_subplan = asyncio.run(
            standard_workflow._generate_round_subplan(
                client=None,
                client_type="openai",
                memory_agent=standard_memory,
                plan_content="plan",
                iteration=6,
            )
        )

        index_workflow = CodeImplementationWorkflowWithIndex.__new__(
            CodeImplementationWorkflowWithIndex
        )
        index_workflow.logger = logging.getLogger("paper_to_code_v1_index_retry_test")
        index_workflow._call_llm_with_tools = should_not_call_llm
        index_memory = FakeRetryMemoryAgent()
        index_subplan = asyncio.run(
            index_workflow._generate_round_subplan(
                client=None,
                client_type="openai",
                memory_agent=index_memory,
                plan_content="plan",
                iteration=6,
            )
        )

        self.assertEqual(standard_memory.requested_target, "pkg/b.py")
        self.assertEqual(index_memory.requested_target, "pkg/b.py")
        self.assertEqual(standard_subplan["target_file"], "pkg/b.py")
        self.assertEqual(index_subplan["target_file"], "pkg/b.py")
        self.assertEqual(standard_subplan["round"], 6)
        self.assertEqual(index_subplan["round"], 6)
        self.assertIn("failed", standard_subplan["goal"].lower())
        self.assertIn("syntax validation failed", standard_subplan["why_now"])

    def test_retry_required_summaries_are_not_added_to_must_use_contracts(self):
        summary_records = [
            {
                "round": 2,
                "status": "retry_required",
                "file": "pkg/bad.py",
                "files": ["pkg/bad.py"],
                "exports": ["MissingInterface"],
                "used_by_hint": ["pkg/target.py"],
                "issues": ["Import contract mismatch"],
            },
            {
                "round": 1,
                "status": "done",
                "file": "pkg/good.py",
                "files": ["pkg/good.py"],
                "exports": ["GoodInterface"],
                "used_by_hint": ["pkg/target.py"],
                "issues": [],
            },
        ]

        for workflow_cls in (
            CodeImplementationWorkflow,
            CodeImplementationWorkflowWithIndex,
        ):
            workflow = workflow_cls.__new__(workflow_cls)
            enriched = workflow._ensure_subplan_contracts(
                {
                    "target_files": ["pkg/target.py"],
                    "file_order": ["pkg/target.py"],
                    "must_use": [],
                    "acceptance_checks": [],
                },
                summary_records,
                ["pkg/target.py"],
            )

            self.assertIn("pkg/good.py", enriched["must_use"])
            self.assertNotIn("pkg/bad.py", enriched["must_use"])

    def test_workflows_abort_repeated_retry_loop(self):
        class RepeatRetryMemory:
            def get_progress_state(self):
                return {
                    "retry_batch": ["pkg/a.py", "pkg/b.py"],
                    "retry_reason": "Import contract mismatch: Missing",
                    "retry_repeat_count": 5,
                }

        for workflow_cls in (
            CodeImplementationWorkflow,
            CodeImplementationWorkflowWithIndex,
        ):
            workflow = workflow_cls.__new__(workflow_cls)
            message = workflow._maybe_abort_repeated_retry_loop(RepeatRetryMemory())

            self.assertIsNotNone(message)
            self.assertIn("repeated 5 times", message)
            self.assertIn("pkg/a.py", message)

    def test_static_contract_check_detects_missing_imported_symbol(self):
        temp_root = ROOT / "tests" / "_tmp_contract_check"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "generate_code" / "demo" / "src" / "attack").mkdir(
            parents=True, exist_ok=True
        )

        try:
            target_module = (
                temp_root
                / "generate_code"
                / "demo"
                / "src"
                / "attack"
                / "relevance_optimizer.py"
            )
            target_module.write_text(
                "class RelevanceOptimizer:\n"
                "    def optimize_relevance(self):\n"
                "        return 'ok'\n",
                encoding="utf-8",
            )

            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.code_directory = str(temp_root / "generate_code")
            memory_agent.logger = logging.getLogger("paper_to_code_v1_contract_test")

            issues = memory_agent._analyze_python_import_contract_issues(
                "demo/src/attack/__init__.py",
                "from .relevance_optimizer import optimize_relevance\n",
            )
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(len(issues), 1)
        self.assertIn("optimize_relevance", issues[0])
        self.assertIn("not defined", issues[0])

    def test_relevant_round_summaries_keep_latest_round_and_escape_hatch_match(self):
        temp_root = ROOT / "tests" / "_tmp_summary_recall"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        round_summaries_path = temp_root / "round_summaries.jsonl"
        records = [
            {
                "round": 12,
                "file": "demo/src/attack/relevance_optimizer.py",
                "exports": ["RelevanceOptimizer", "create_relevance_optimizer"],
                "deps": [],
                "used_by_hint": ["demo/src/attack/__init__.py"],
                "issues": [],
                "next_hint": "",
                "notes": "attack helper",
            },
            {
                "round": 13,
                "file": "demo/src/attack/persuasion_optimizer.py",
                "exports": ["optimize_persuasion"],
                "deps": [],
                "used_by_hint": ["demo/src/attack/__init__.py"],
                "issues": [],
                "next_hint": "",
                "notes": "attack helper",
            },
            {
                "round": 28,
                "file": "demo/experiments/main_results.py",
                "exports": ["main"],
                "deps": [],
                "used_by_hint": [],
                "issues": [],
                "next_hint": "",
                "notes": "late unrelated file",
            },
        ]
        round_summaries_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
            + "\n",
            encoding="utf-8",
        )

        try:
            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.round_summaries_path = str(round_summaries_path)
            memory_agent.logger = logging.getLogger("paper_to_code_v1_recall_test")
            memory_agent.get_progress_state = lambda: {
                "pending_files": ["demo/src/attack/__init__.py"],
                "retry_target": None,
                "current_target": "demo/src/attack/__init__.py",
            }

            summaries = memory_agent.get_relevant_round_summaries(
                target_file="demo/src/attack/__init__.py",
                max_records=2,
            )
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(
            [record["file"] for record in summaries],
            [
                "demo/src/attack/persuasion_optimizer.py",
                "demo/experiments/main_results.py",
            ],
        )

    def test_retry_required_summaries_do_not_export_failed_contracts(self):
        temp_root = ROOT / "tests" / "_tmp_retry_summary_sanitize"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.logger = logging.getLogger("paper_to_code_v2_retry_summary_test")
            memory_agent.current_round = 9
            memory_agent.all_files_list = ["pkg/consumer.py", "pkg/provider.py"]
            memory_agent.implemented_files = []
            memory_agent.save_path = str(temp_root)
            memory_agent.subplans_dir = str(temp_root / "subplans")
            memory_agent.round_summaries_path = str(temp_root / "round_summaries.jsonl")
            memory_agent.implementation_progress_path = str(
                temp_root / "implementation_progress.json"
            )
            memory_agent._ensure_execution_artifacts()
            memory_agent.initialize_progress_state(force=True)

            asyncio.run(
                memory_agent._append_round_summary_record(
                    {
                        "round": 9,
                        "file": "pkg/consumer.py",
                        "files": ["pkg/consumer.py"],
                        "goal": "retry consumer",
                        "status": "retry_required",
                        "exports": ["MissingInterface"],
                        "deps": ["pkg/provider.py"],
                        "exports_by_file": {"pkg/consumer.py": ["MissingInterface"]},
                        "deps_by_file": {"pkg/consumer.py": ["pkg/provider.py"]},
                        "used_by_hint": ["pkg/provider.py"],
                        "issues": ["Import contract mismatch: MissingInterface"],
                        "next_hint": "",
                        "summary_context_rounds": [],
                    }
                )
            )

            records = [
                json.loads(line)
                for line in (temp_root / "round_summaries.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(records[0]["status"], "retry_required")
        self.assertEqual(records[0]["exports"], [])
        self.assertEqual(records[0]["deps"], [])
        self.assertEqual(records[0]["exports_by_file"], {})
        self.assertEqual(records[0]["deps_by_file"], {})
        self.assertEqual(records[0]["used_by_hint"], [])
        self.assertIn("Import contract mismatch", records[0]["issues"][0])

    def test_retry_required_recall_keeps_issues_but_no_contracts(self):
        temp_root = ROOT / "tests" / "_tmp_retry_recall_sanitize"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        records = [
            {
                "round": 4,
                "status": "done",
                "file": "pkg/provider.py",
                "files": ["pkg/provider.py"],
                "exports": ["Provider"],
                "deps": [],
                "exports_by_file": {"pkg/provider.py": ["Provider"]},
                "deps_by_file": {},
                "used_by_hint": ["pkg/consumer.py"],
                "issues": [],
                "summary_context_rounds": [],
            },
            {
                "round": 5,
                "status": "retry_required",
                "file": "pkg/consumer.py",
                "files": ["pkg/consumer.py"],
                "exports": ["MissingInterface"],
                "deps": ["pkg/provider.py"],
                "exports_by_file": {"pkg/consumer.py": ["MissingInterface"]},
                "deps_by_file": {"pkg/consumer.py": ["pkg/provider.py"]},
                "used_by_hint": ["pkg/provider.py"],
                "issues": ["Import contract mismatch: MissingInterface"],
                "summary_context_rounds": [4],
            },
        ]
        (temp_root / "round_summaries.jsonl").write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
            + "\n",
            encoding="utf-8",
        )

        try:
            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.round_summaries_path = str(temp_root / "round_summaries.jsonl")
            memory_agent.logger = logging.getLogger("paper_to_code_v2_retry_recall_test")
            memory_agent.get_progress_state = lambda: {
                "pending_files": ["pkg/consumer.py"],
                "retry_target": "pkg/consumer.py",
                "current_target": "pkg/consumer.py",
            }

            summaries = memory_agent.get_relevant_round_summaries(
                target_file="pkg/consumer.py",
                max_records=3,
            )
            execution_memory = memory_agent.format_round_summaries_for_execution(
                summaries
            )
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        retry_summary = next(
            record for record in summaries if record["status"] == "retry_required"
        )
        self.assertEqual(retry_summary["exports"], [])
        self.assertEqual(retry_summary["deps"], [])
        self.assertEqual(retry_summary["exports_by_file"], {})
        self.assertIn("Import contract mismatch", retry_summary["issues"][0])
        self.assertIn("status: retry_required", execution_memory)
        self.assertIn("issues: Import contract mismatch", execution_memory)

    def test_record_failed_file_attempt_requeues_completed_file(self):
        temp_root = ROOT / "tests" / "_tmp_retry_requeue"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.logger = logging.getLogger("paper_to_code_v1_requeue_test")
            memory_agent.current_round = 4
            memory_agent.all_files_list = ["pkg/a.py", "pkg/b.py"]
            memory_agent.implemented_files = ["pkg/a.py", "pkg/b.py"]
            memory_agent.implementation_progress_path = str(
                temp_root / "implementation_progress.json"
            )
            memory_agent.subplans_dir = str(temp_root / "subplans")
            memory_agent.save_path = str(temp_root)
            memory_agent._ensure_execution_artifacts()
            memory_agent._save_progress_state(
                {
                    "round": 4,
                    "status": "running",
                    "all_files": ["pkg/a.py", "pkg/b.py"],
                    "completed_files": ["pkg/a.py", "pkg/b.py"],
                    "pending_files": [],
                    "current_target": "pkg/b.py",
                    "last_subplan": "subplans/round_004.json",
                    "last_summary_round": 4,
                    "retry_target": None,
                    "retry_reason": None,
                }
            )

            progress_state = memory_agent.record_failed_file_attempt(
                "pkg/b.py",
                "Blocking issues detected after summary validation: example",
            )
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(progress_state["retry_target"], "pkg/b.py")
        self.assertIn("pkg/b.py", progress_state["pending_files"])
        self.assertNotIn("pkg/b.py", progress_state["completed_files"])
        self.assertNotIn("pkg/b.py", memory_agent.implemented_files)

    def test_record_failed_batch_attempt_counts_repeated_retry_signature(self):
        temp_root = ROOT / "tests" / "_tmp_retry_repeat_count"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.logger = logging.getLogger("paper_to_code_v2_retry_repeat_test")
            memory_agent.current_round = 2
            memory_agent.all_files_list = ["pkg/a.py", "pkg/b.py"]
            memory_agent.implemented_files = ["pkg/a.py", "pkg/b.py"]
            memory_agent.save_path = str(temp_root)
            memory_agent.subplans_dir = str(temp_root / "subplans")
            memory_agent.round_summaries_path = str(temp_root / "round_summaries.jsonl")
            memory_agent.implementation_progress_path = str(
                temp_root / "implementation_progress.json"
            )
            memory_agent._ensure_execution_artifacts()
            memory_agent.initialize_progress_state(force=True)

            first_state = memory_agent.record_failed_batch_attempt(
                ["pkg/b.py"], "Import contract mismatch: Missing"
            )
            second_state = memory_agent.record_failed_batch_attempt(
                ["pkg/b.py"], "Import contract mismatch: Missing"
            )
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(first_state["retry_repeat_count"], 1)
        self.assertEqual(second_state["retry_repeat_count"], 2)
        self.assertEqual(
            first_state["retry_signature"], second_state["retry_signature"]
        )

    def test_update_progress_after_write_preserves_existing_retry_state(self):
        temp_root = ROOT / "tests" / "_tmp_update_progress_retry_state"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.logger = logging.getLogger("paper_to_code_v2_update_progress_test")
            memory_agent.current_round = 7
            memory_agent.all_files_list = ["pkg/a.py", "pkg/b.py"]
            memory_agent.implemented_files = []
            memory_agent.save_path = str(temp_root)
            memory_agent.subplans_dir = str(temp_root / "subplans")
            memory_agent.round_summaries_path = str(temp_root / "round_summaries.jsonl")
            memory_agent.implementation_progress_path = str(
                temp_root / "implementation_progress.json"
            )
            memory_agent._ensure_execution_artifacts()
            memory_agent._save_progress_state(
                {
                    "round": 7,
                    "status": "running",
                    "all_files": ["pkg/a.py", "pkg/b.py"],
                    "completed_files": [],
                    "pending_files": ["pkg/a.py", "pkg/b.py"],
                    "current_target": "pkg/b.py",
                    "current_targets": ["pkg/b.py"],
                    "last_subplan": "subplans/round_007.json",
                    "last_summary_round": 6,
                    "retry_target": "pkg/b.py",
                    "retry_batch": ["pkg/b.py"],
                    "retry_reason": "target file was not written",
                    "retry_signature": "abc123",
                    "retry_repeat_count": 4,
                }
            )

            progress_state = memory_agent.update_progress_after_write("pkg/a.py")
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(progress_state["retry_target"], "pkg/b.py")
        self.assertEqual(progress_state["retry_batch"], ["pkg/b.py"])
        self.assertEqual(progress_state["retry_reason"], "target file was not written")
        self.assertEqual(progress_state["retry_signature"], "abc123")
        self.assertEqual(progress_state["retry_repeat_count"], 4)

    def test_create_round_batch_summary_only_summarizes_written_files_in_batch(self):
        temp_root = ROOT / "tests" / "_tmp_partial_batch_summary"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "generate_code" / "pkg").mkdir(parents=True, exist_ok=True)

        try:
            written_file = temp_root / "generate_code" / "pkg" / "a.py"
            written_file.write_text(
                "def build_a():\n    return 'ok'\n",
                encoding="utf-8",
            )

            memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
            memory_agent.logger = logging.getLogger("paper_to_code_v2_partial_batch_test")
            memory_agent.current_round = 3
            memory_agent.all_files_list = ["pkg/a.py", "pkg/b.py"]
            memory_agent.implemented_files = ["pkg/a.py"]
            memory_agent.code_directory = str(temp_root / "generate_code")
            memory_agent.save_path = str(temp_root)
            memory_agent.subplans_dir = str(temp_root / "subplans")
            memory_agent.round_summaries_path = str(temp_root / "round_summaries.jsonl")
            memory_agent.implementation_progress_path = str(
                temp_root / "implementation_progress.json"
            )
            memory_agent._ensure_execution_artifacts()
            memory_agent.initialize_progress_state(force=True)

            summary = asyncio.run(
                memory_agent.create_round_batch_summary(
                    client=None,
                    client_type="openai",
                    subplan={
                        "round": 3,
                        "goal": "implement batch",
                        "target_files": ["pkg/a.py", "pkg/b.py"],
                        "summary_context_rounds": [],
                    },
                    files_written_this_round=["pkg/a.py"],
                )
            )
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        self.assertEqual(summary["status"], "done")
        self.assertEqual(summary["files"], ["pkg/a.py"])
        self.assertNotIn("pkg/b.py", summary["files"])
        self.assertEqual(summary["issues"], [])

    def test_progress_state_keeps_extra_completed_files_in_ledger(self):
        memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
        memory_agent.logger = logging.getLogger("paper_to_code_v1_progress_ledger_test")
        memory_agent.current_round = 3
        memory_agent.all_files_list = ["pkg/a.py"]

        progress_state = memory_agent._build_progress_state(
            completed_files=["pkg/a.py", "pkg/config/__init__.py"]
        )

        self.assertIn("pkg/config/__init__.py", progress_state["all_files"])
        self.assertEqual(
            progress_state["completed_files"],
            ["pkg/a.py", "pkg/config/__init__.py"],
        )
        self.assertEqual(progress_state["pending_files"], [])

    def test_merge_summary_exports_uses_local_python_bindings(self):
        memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
        summary = {
            "exports": ["ExistingName"],
        }

        additional_exports = memory_agent._extract_python_bindings_from_content(
            "from .helper import build_helper\n"
            "class Demo:\n"
            "    pass\n"
            "def create_demo():\n"
            "    return Demo()\n",
            "pkg/__init__.py",
        )
        merged_summary = memory_agent._merge_summary_exports(summary, additional_exports)

        self.assertIn("ExistingName", merged_summary["exports"])
        self.assertIn("build_helper", merged_summary["exports"])
        self.assertIn("Demo", merged_summary["exports"])
        self.assertIn("create_demo", merged_summary["exports"])

    def test_failed_write_file_result_is_not_tracked_as_completed(self):
        agent = CodeImplementationAgent(
            mcp_agent=None,
            logger=logging.getLogger("paper_to_code_v1_failed_write_tracking_test"),
            enable_read_tools=True,
        )

        agent._track_file_implementation(
            {
                "input": {
                    "file_path": "pkg/a.py",
                    "content": "def broken(:\n    pass\n",
                }
            },
            {
                "status": "error",
                "file_path": "pkg/a.py",
                "message": "write_file Python syntax validation failed",
            },
        )

        self.assertEqual(agent.get_files_implemented_count(), 0)
        self.assertEqual(
            agent.get_implementation_summary()["completed_files"],
            [],
        )

    def test_standard_final_report_parses_call_tool_result_history(self):
        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = logging.getLogger("paper_to_code_v1_final_report_test")
        workflow.mcp_agent = FakeMCPHistoryAgent()

        report = asyncio.run(
            workflow._generate_pure_code_final_report_with_concise_agents(
                iterations=2,
                elapsed_time=1.5,
                code_agent=FakeStatsCodeAgent(),
                memory_agent=FakeStatsMemoryAgent(),
            )
        )

        self.assertIn("File write operations: 1", report)
        self.assertIn("Total MCP operations: 3", report)
        self.assertIn("- pkg/a.py", report)

    def test_final_report_does_not_underreport_write_operations_when_history_is_truncated(self):
        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = logging.getLogger("paper_to_code_v1_final_report_truncated_test")
        workflow.mcp_agent = FakeMCPHistoryAgent()

        report = asyncio.run(
            workflow._generate_pure_code_final_report_with_concise_agents(
                iterations=36,
                elapsed_time=12.0,
                code_agent=FakeStatsCodeAgentManyWrites(),
                memory_agent=FakeStatsMemoryAgent(),
            )
        )

        self.assertIn("File write operations: 36", report)

    def test_final_report_deduplicates_files_created_listing(self):
        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = logging.getLogger("paper_to_code_v1_final_report_dedupe_test")
        workflow.mcp_agent = FakeMCPHistoryAgentDuplicateWrites()

        report = asyncio.run(
            workflow._generate_pure_code_final_report_with_concise_agents(
                iterations=3,
                elapsed_time=2.0,
                code_agent=FakeStatsCodeAgent(),
                memory_agent=FakeStatsMemoryAgent(),
            )
        )

        self.assertEqual(report.count("- pkg/a.py"), 1)
        self.assertEqual(report.count("- pkg/b.py"), 1)
        self.assertIn("File write operations: 3", report)

    def test_index_final_report_deduplicates_files_created_listing(self):
        workflow = CodeImplementationWorkflowWithIndex.__new__(
            CodeImplementationWorkflowWithIndex
        )
        workflow.logger = logging.getLogger(
            "paper_to_code_v1_index_final_report_dedupe_test"
        )
        workflow.mcp_agent = FakeMCPHistoryAgentDuplicateWrites()

        report = asyncio.run(
            workflow._generate_pure_code_final_report_with_concise_agents(
                iterations=3,
                elapsed_time=2.0,
                code_agent=FakeStatsCodeAgent(),
                memory_agent=FakeStatsMemoryAgent(),
            )
        )

        self.assertEqual(report.count("- pkg/a.py"), 1)
        self.assertEqual(report.count("- pkg/b.py"), 1)
        self.assertIn("File write operations: 3", report)

    def test_repository_ruff_validation_uses_no_cache(self):
        memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
        memory_agent.logger = logging.getLogger("paper_to_code_v1_ruff_no_cache_test")
        memory_agent.code_directory = str(ROOT)

        with mock.patch(
            "paper_to_code_v1_memory_agent_test_module.shutil.which",
            return_value="ruff",
        ), mock.patch(
            "paper_to_code_v1_memory_agent_test_module.subprocess.run"
        ) as mocked_run:
            mocked_run.return_value = mock.Mock(
                returncode=0,
                stdout="[]",
                stderr="",
            )
            memory_agent._run_blocking_ruff_checks(["tests/test_paper_to_code_v1_regressions.py"])

        args = mocked_run.call_args.args[0]
        self.assertIn("--no-cache", args)

    def test_write_file_validation_catches_compile_only_python_error(self):
        agent = CodeImplementationAgent.__new__(CodeImplementationAgent)
        agent.logger = logging.getLogger("paper_to_code_v1_compile_guard_test")

        result = agent._validate_write_file_input(
            {
                "file_path": "pkg/bad.py",
                "content": "def broken():\n    await call()\n",
            }
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["validation"], "python_syntax")
        self.assertIn("outside async", result["message"])

    def test_repository_validation_catches_compile_only_python_error(self):
        memory_agent = ConciseMemoryAgent.__new__(ConciseMemoryAgent)
        memory_agent.logger = logging.getLogger(
            "paper_to_code_v1_repository_compile_guard_test"
        )
        memory_agent.code_directory = str(ROOT)

        issues = memory_agent._collect_python_validation_issues_for_file(
            "pkg/bad.py",
            "def broken():\n    await call()\n",
        )

        self.assertTrue(any("outside async" in issue for issue in issues))

    def test_normalize_placeholder_notebooks_fills_empty_files(self):
        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = FakeWorkflowLogger()

        temp_root = ROOT / "tests" / "_tmp_notebook_normalization"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        notebook_path = temp_root / "generate_code" / "pkg" / "analysis.ipynb"
        notebook_path.parent.mkdir(parents=True, exist_ok=True)
        notebook_path.write_text("", encoding="utf-8")

        try:
            normalized_files = workflow._normalize_placeholder_notebooks(
                str(temp_root / "generate_code")
            )
            self.assertEqual(normalized_files, [str(notebook_path)])

            notebook_json = json.loads(notebook_path.read_text(encoding="utf-8"))
            self.assertEqual(notebook_json["nbformat"], 4)
            self.assertEqual(notebook_json["cells"], [])
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

    def test_run_code_analyzer_logs_user_image_context_without_context_object_error(self):
        logger = StrictLogger()
        paper_dir = ROOT / "tests" / "_tmp_code_analyzer_logger"
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
        paper_dir.mkdir(parents=True, exist_ok=True)

        try:
            paper_file = paper_dir / "paper.md"
            paper_file.write_text("paper body", encoding="utf-8")

            with mock.patch.object(
                orchestration_module,
                "_find_primary_paper_markdown_file",
                return_value=str(paper_file),
            ), mock.patch.object(
                orchestration_module,
                "_load_optional_json_file",
                return_value={
                    "selected_page": 2,
                    "selected_image_path": "figure_assets/page_snapshots/page_002.png",
                },
            ), mock.patch.object(
                orchestration_module,
                "get_search_server_names",
                return_value=[],
            ), mock.patch.object(
                orchestration_module,
                "get_adaptive_agent_config",
                return_value={},
            ), mock.patch.object(
                orchestration_module,
                "get_adaptive_prompts",
                return_value={
                    "concept_analysis": "concept",
                    "algorithm_analysis": "algorithm",
                    "code_planning": "planner",
                },
            ), mock.patch.object(
                orchestration_module,
                "Agent",
                FakeAgent,
            ), mock.patch.object(
                orchestration_module,
                "get_preferred_llm_class",
                return_value=object,
            ), mock.patch.object(
                orchestration_module,
                "get_token_limits",
                return_value=(1024, 512),
            ), mock.patch.object(
                orchestration_module,
                "_build_code_analysis_message",
                return_value="concept-message",
            ), mock.patch.object(
                orchestration_module,
                "_build_multimodal_algorithm_message",
                return_value="algorithm-message",
            ), mock.patch.object(
                orchestration_module,
                "_build_code_planner_input_message",
                return_value="planner-message",
            ), mock.patch.object(
                orchestration_module,
                "_assess_output_completeness",
                return_value=1.0,
            ):
                result = asyncio.run(
                    orchestration_module.run_code_analyzer(
                        str(paper_dir),
                        logger,
                        use_segmentation=False,
                        user_image_context_path=str(
                            paper_dir / "user_image_context.json"
                        ),
                    )
                )
        finally:
            if paper_dir.exists():
                shutil.rmtree(paper_dir)

        self.assertEqual(result, "planner-output")
        self.assertTrue(
            any(
                "Loaded user image context: page=2 image=figure_assets/page_snapshots/page_002.png"
                in message
                for level, message in logger.messages
                if level == "info"
            )
        )

    def test_run_code_analyzer_uses_no_search_servers_when_paper_content_preloaded(self):
        logger = StrictLogger()
        paper_dir = ROOT / "tests" / "_tmp_code_analyzer_no_search"
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
        paper_dir.mkdir(parents=True, exist_ok=True)
        RecordingAgent.instances = []

        try:
            paper_file = paper_dir / "paper.md"
            paper_file.write_text("paper body", encoding="utf-8")

            with mock.patch.object(
                orchestration_module,
                "_find_primary_paper_markdown_file",
                return_value=str(paper_file),
            ), mock.patch.object(
                orchestration_module,
                "get_search_server_names",
                return_value=["brave"],
            ), mock.patch.object(
                orchestration_module,
                "get_adaptive_agent_config",
                return_value={},
            ), mock.patch.object(
                orchestration_module,
                "get_adaptive_prompts",
                return_value={
                    "concept_analysis": "concept",
                    "algorithm_analysis": "algorithm",
                    "code_planning": "planner",
                },
            ), mock.patch.object(
                orchestration_module,
                "Agent",
                RecordingAgent,
            ), mock.patch.object(
                orchestration_module,
                "get_preferred_llm_class",
                return_value=object,
            ), mock.patch.object(
                orchestration_module,
                "get_token_limits",
                return_value=(1024, 512),
            ), mock.patch.object(
                orchestration_module,
                "_build_code_analysis_message",
                return_value="concept-message",
            ), mock.patch.object(
                orchestration_module,
                "_build_code_planner_input_message",
                return_value="planner-message",
            ), mock.patch.object(
                orchestration_module,
                "_assess_output_completeness",
                return_value=1.0,
            ):
                result = asyncio.run(
                    orchestration_module.run_code_analyzer(
                        str(paper_dir),
                        logger,
                        use_segmentation=False,
                    )
                )
        finally:
            if paper_dir.exists():
                shutil.rmtree(paper_dir)

        self.assertEqual(result, "planner-output")
        self.assertEqual(RecordingAgent.instances[0].server_names, [])
        self.assertEqual(RecordingAgent.instances[1].server_names, [])
        self.assertEqual(RecordingAgent.instances[2].server_names, [])

    def test_requirement_analysis_error_logging_uses_plain_message(self):
        logger = StrictLogger()

        with mock.patch.object(
            orchestration_module,
            "RequirementAnalysisAgent",
            FakeRequirementAnalysisAgent,
        ):
            result = asyncio.run(
                orchestration_module.execute_requirement_analysis_workflow(
                    user_input="build a demo",
                    analysis_mode="invalid_mode",
                    logger=logger,
                )
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("Unsupported analysis_mode", result["error"])
        self.assertTrue(
            any(
                "Requirement analysis workflow failed: Unsupported analysis_mode: invalid_mode"
                in message
                for level, message in logger.messages
                if level == "error"
            )
        )

    def test_standard_workflow_uses_v1_subplan_loop_without_indexing(self):
        FakeWorkflowMemoryAgent.instances.clear()
        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = logging.getLogger("paper_to_code_v1_standard_workflow_test")
        workflow.enable_read_tools = True
        workflow.mcp_agent = object()
        workflow.default_models = {}

        captured_calls = []

        async def fake_generate_round_subplan(
            client, client_type, memory_agent, plan_content, iteration
        ):
            return {
                "round": iteration,
                "target_file": "pkg/a.py",
                "goal": "implement target file",
                "why_now": "first pending file",
                "must_implement": ["write pkg/a.py"],
                "must_use": [],
                "must_not_break": ["keep interfaces stable"],
                "acceptance_checks": ["file exists"],
                "summary_context_rounds": [],
                "recalled_summaries": [
                    {
                        "file": "pkg/helper.py",
                        "exports": ["build_helper"],
                        "deps": ["pkg/config.py"],
                        "used_by_hint": ["pkg/a.py"],
                        "issues": [],
                    }
                ],
                "subplan_path": "subplans/round_001.json",
            }

        async def fake_call_llm_with_tools(
            client,
            client_type,
            system_message,
            messages,
            tools,
            max_tokens=8192,
            model_role="implementation",
        ):
            captured_calls.append(
                {
                    "client_type": client_type,
                    "system_message": system_message,
                    "messages": messages,
                    "model_role": model_role,
                }
            )
            return {
                "content": "implemented",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "input": {"file_path": "pkg/a.py", "content": "print('ok')"},
                    }
                ],
            }

        async def fake_final_report(iteration, elapsed, code_agent, memory_agent):
            return {"status": "ok", "iteration": iteration}

        workflow._generate_round_subplan = fake_generate_round_subplan
        workflow._call_llm_with_tools = fake_call_llm_with_tools
        workflow._generate_pure_code_final_report_with_concise_agents = (
            fake_final_report
        )
        workflow._check_tool_results_for_errors = lambda tool_results: False
        workflow._generate_success_guidance = lambda files_count: "success"
        workflow._generate_error_guidance = lambda: "error"
        workflow._generate_no_tools_guidance = lambda files_count: "no tools"
        workflow._compile_user_response = lambda tool_results, guidance: guidance
        workflow._validate_messages = lambda messages: messages

        workflow_module = sys.modules["paper_to_code_v1_standard_workflow_test_module"]
        with mock.patch.object(
            workflow_module, "CodeImplementationAgent", FakeWorkflowCodeAgent
        ), mock.patch.object(
            workflow_module, "ConciseMemoryAgent", FakeWorkflowMemoryAgent
        ):
            result = asyncio.run(
                workflow._pure_code_implementation_loop(
                    client=None,
                    client_type="openai",
                    system_message="system",
                    messages=[{"role": "user", "content": "start"}],
                    tools=[],
                    plan_content="plan",
                    target_directory=str(ROOT / "tests"),
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(captured_calls), 1)
        self.assertEqual(captured_calls[0]["model_role"], "sub_implementation")
        self.assertTrue(
            any(
                "Subplan Path: subplans/round_001.json"
                in message.get("content", "")
                for message in captured_calls[0]["messages"]
                if message.get("role") == "user"
            )
        )
        self.assertTrue(
            any(
                "Relevant Existing Interfaces" in message.get("content", "")
                and "pkg/helper.py" in message.get("content", "")
                and "build_helper" in message.get("content", "")
                for message in captured_calls[0]["messages"]
                if message.get("role") == "user"
            )
        )
        self.assertEqual(FakeWorkflowMemoryAgent.instances[0].rounds, [0, 1])

    def test_standard_workflow_requeues_repository_gate_failures_before_finish(self):
        class GateMemoryAgent(FakeWorkflowMemoryAgent):
            instances = []

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.validation_calls = 0
                self.retry_calls = []
                GateMemoryAgent.instances.append(self)

            def validate_generated_repository(self):
                self.validation_calls += 1
                if self.validation_calls == 1:
                    return [
                        {
                            "file": "pkg/a.py",
                            "issues": ["pkg/a.py:3: F821 Undefined name `helper`"],
                        }
                    ]
                return []

            def record_failed_file_attempt(self, file_path, message):
                self.retry_calls.append((file_path, message))
                return {"retry_target": file_path, "pending_files": [file_path]}

        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = logging.getLogger("paper_to_code_v1_repo_gate_test")
        workflow.enable_read_tools = True
        workflow.mcp_agent = object()
        workflow.default_models = {}

        llm_calls = []

        async def fake_generate_round_subplan(
            client, client_type, memory_agent, plan_content, iteration
        ):
            return {
                "round": iteration,
                "target_file": "pkg/a.py",
                "goal": "implement target file",
                "why_now": "first pending file",
                "must_implement": ["write pkg/a.py"],
                "must_use": [],
                "must_not_break": ["keep interfaces stable"],
                "acceptance_checks": ["file exists"],
                "summary_context_rounds": [],
                "recalled_summaries": [],
                "subplan_path": f"subplans/round_{iteration:03d}.json",
            }

        async def fake_call_llm_with_tools(
            client,
            client_type,
            system_message,
            messages,
            tools,
            max_tokens=8192,
            model_role="implementation",
        ):
            llm_calls.append([message.get("content", "") for message in messages])
            if len(llm_calls) == 1:
                return {
                    "content": "implemented",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "write_file",
                            "input": {
                                "file_path": "pkg/a.py",
                                "content": "print('ok')",
                            },
                        }
                    ],
                }
            return {"content": "done", "tool_calls": []}

        async def fake_final_report(iteration, elapsed, code_agent, memory_agent):
            return {"status": "ok", "iteration": iteration}

        workflow._generate_round_subplan = fake_generate_round_subplan
        workflow._call_llm_with_tools = fake_call_llm_with_tools
        workflow._generate_pure_code_final_report_with_concise_agents = (
            fake_final_report
        )
        workflow._check_tool_results_for_errors = lambda tool_results: False
        workflow._generate_success_guidance = lambda files_count: "success"
        workflow._generate_error_guidance = lambda: "error"
        workflow._generate_no_tools_guidance = lambda files_count: "no tools"
        workflow._compile_user_response = lambda tool_results, guidance: guidance
        workflow._validate_messages = lambda messages: messages

        workflow_module = sys.modules["paper_to_code_v1_standard_workflow_test_module"]
        with mock.patch.object(
            workflow_module, "CodeImplementationAgent", FakeWorkflowCodeAgent
        ), mock.patch.object(
            workflow_module, "ConciseMemoryAgent", GateMemoryAgent
        ):
            result = asyncio.run(
                workflow._pure_code_implementation_loop(
                    client=None,
                    client_type="openai",
                    system_message="system",
                    messages=[{"role": "user", "content": "start"}],
                    tools=[],
                    plan_content="plan",
                    target_directory=str(ROOT / "tests"),
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(GateMemoryAgent.instances[0].validation_calls, 2)
        self.assertEqual(
            GateMemoryAgent.instances[0].retry_calls[0][0],
            "pkg/a.py",
        )
        self.assertTrue(
            any(
                "Repository completion gate failed" in message
                for batch in llm_calls[1:]
                for message in batch
            )
        )

    def test_index_workflow_propagates_index_system_prompt_into_execution_loop(self):
        FakeWorkflowMemoryAgent.instances.clear()
        workflow = CodeImplementationWorkflowWithIndex.__new__(
            CodeImplementationWorkflowWithIndex
        )
        workflow.logger = logging.getLogger("paper_to_code_v1_index_prompt_test")
        workflow.enable_read_tools = True
        workflow.mcp_agent = object()
        workflow.default_models = {}

        captured_calls = []

        async def fake_generate_round_subplan(
            client, client_type, memory_agent, plan_content, iteration
        ):
            return {
                "round": iteration,
                "target_file": "pkg/a.py",
                "goal": "implement target file",
                "why_now": "first pending file",
                "must_implement": ["write pkg/a.py"],
                "must_use": [],
                "must_not_break": ["keep interfaces stable"],
                "acceptance_checks": ["file exists"],
                "summary_context_rounds": [],
                "recalled_summaries": [
                    {
                        "file": "pkg/helper.py",
                        "exports": ["build_helper"],
                        "deps": ["pkg/config.py"],
                        "used_by_hint": ["pkg/a.py"],
                        "issues": [],
                    }
                ],
                "subplan_path": "subplans/round_001.json",
            }

        async def fake_call_llm_with_tools(
            client,
            client_type,
            system_message,
            messages,
            tools,
            max_tokens=8192,
            model_role="implementation",
        ):
            captured_calls.append(
                {
                    "client_type": client_type,
                    "system_message": system_message,
                    "messages": messages,
                    "model_role": model_role,
                }
            )
            return {
                "content": "implemented",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "input": {"file_path": "pkg/a.py", "content": "print('ok')"},
                    }
                ],
            }

        async def fake_final_report(iteration, elapsed, code_agent, memory_agent):
            return {"status": "ok", "iteration": iteration}

        workflow._generate_round_subplan = fake_generate_round_subplan
        workflow._call_llm_with_tools = fake_call_llm_with_tools
        workflow._generate_pure_code_final_report_with_concise_agents = (
            fake_final_report
        )
        workflow._check_tool_results_for_errors = lambda tool_results: False
        workflow._generate_success_guidance = lambda files_count: "success"
        workflow._generate_error_guidance = lambda: "error"
        workflow._generate_no_tools_guidance = lambda files_count: "no tools"
        workflow._compile_user_response = lambda tool_results, guidance: guidance
        workflow._validate_messages = lambda messages: messages

        workflow_module = sys.modules["paper_to_code_v1_index_workflow_test_module"]
        with mock.patch.object(
            workflow_module, "CodeImplementationAgent", FakeWorkflowCodeAgent
        ), mock.patch.object(
            workflow_module, "ConciseMemoryAgent", FakeWorkflowMemoryAgent
        ):
            result = asyncio.run(
                workflow._pure_code_implementation_loop(
                    client=None,
                    client_type="openai",
                    system_message="index-system",
                    messages=[{"role": "user", "content": "start"}],
                    tools=[],
                    plan_content="plan",
                    target_directory=str(ROOT / "tests"),
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(captured_calls), 1)
        self.assertEqual(captured_calls[0]["model_role"], "sub_implementation")
        self.assertEqual(captured_calls[0]["system_message"], "index-system")
        self.assertTrue(
            any(
                "Relevant Existing Interfaces" in message.get("content", "")
                and "pkg/helper.py" in message.get("content", "")
                and "build_helper" in message.get("content", "")
                for message in captured_calls[0]["messages"]
                if message.get("role") == "user"
            )
        )

    def test_standard_workflow_initializes_without_reference_indexer_server(self):
        FakeInitAgent.instances.clear()
        workflow = CodeImplementationWorkflow.__new__(CodeImplementationWorkflow)
        workflow.logger = logging.getLogger("paper_to_code_v1_standard_init_test")
        workflow.config_path = "mcp_agent.secrets.yaml"
        workflow.mcp_agent = None

        workflow_module = sys.modules["paper_to_code_v1_standard_workflow_test_module"]
        with mock.patch.object(
            workflow_module, "Agent", FakeInitAgent
        ), mock.patch.object(
            workflow_module, "get_preferred_llm_class", return_value=object
        ):
            asyncio.run(workflow._initialize_mcp_agent("tests"))

        self.assertEqual(len(FakeInitAgent.instances), 1)
        self.assertEqual(
            FakeInitAgent.instances[0].server_names,
            ["code-implementation"],
        )

if __name__ == "__main__":
    unittest.main()
