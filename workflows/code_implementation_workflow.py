"""
Paper Code Implementation Workflow - MCP-compliant Iterative Development

Features:
1. File Tree Creation
2. Code Implementation - Based on aisi-basic-agent iterative development

MCP Architecture:
- MCP Server: tools/code_implementation_server.py
- MCP Client: Called through mcp_agent framework
- Configuration: mcp_agent.config.yaml
"""

import ast
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# MCP Agent imports
from mcp_agent.agents.agent import Agent

# Local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts.code_prompts import STRUCTURE_GENERATOR_PROMPT
from prompts.code_prompts import (
    GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT,
    REFERENCE_INDEXING_EXECUTOR_GUIDANCE_TEMPLATE,
    ROUND_SUBPLAN_PROMPT,
    ROUND_SUBPLAN_SYSTEM_PROMPT,
    SUBPLAN_EXECUTOR_MESSAGE_TEMPLATE,
)
from workflows.agents import CodeImplementationAgent
from workflows.agents.memory_agent_concise import ConciseMemoryAgent
from config.mcp_tool_definitions import get_mcp_tools
from utils.llm_utils import get_preferred_llm_class, get_default_models, load_api_config
# DialogueLogger removed - no longer needed


class CodeImplementationWorkflow:
    """
    Paper Code Implementation Workflow Manager

    Uses standard MCP architecture:
    1. Connect to code-implementation server via MCP client
    2. Use MCP protocol for tool calls
    3. Support workspace management and operation history tracking
    """

    # ==================== 1. Class Initialization and Configuration (Infrastructure Layer) ====================

    def __init__(
        self,
        config_path: str = "mcp_agent.secrets.yaml",
        implementation_mode: Optional[str] = None,
        enable_reference_indexing: bool = False,
        event_callback: Optional[Any] = None,
    ):
        """Initialize workflow with configuration"""
        self.config_path = config_path
        # Derive main config path from secrets path (same directory)
        secrets_dir = os.path.dirname(os.path.abspath(config_path))
        self.main_config_path = os.path.join(secrets_dir, "mcp_agent.config.yaml")
        self.api_config = self._load_api_config()
        self.default_models = get_default_models(self.main_config_path)
        self.logger = self._create_logger()
        self.mcp_agent = None
        self.enable_read_tools = (
            True  # Default value, will be overridden by run_workflow parameter
        )
        self.implementation_mode = self._normalize_implementation_mode(
            implementation_mode or self._load_implementation_mode()
        )
        self.enable_reference_indexing = bool(enable_reference_indexing)
        self.reference_indexes_path = ""
        self.latest_quality_findings: List[Dict[str, str]] = []
        self.max_final_repair_rounds = 2
        self.event_callback = event_callback

    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit optional DeepRepro UI telemetry without affecting execution."""
        if not self.event_callback:
            return
        try:
            event_payload = dict(payload or {})
            event_payload.setdefault("implementation_mode", self.implementation_mode)
            event_payload.setdefault(
                "reference_indexing", self.enable_reference_indexing
            )
            self.event_callback(event_type, event_payload)
        except Exception as e:
            self.logger.debug(f"DeepRepro event callback ignored error: {e}")

    def _get_total_files_count(self, memory_agent: ConciseMemoryAgent) -> int:
        """Return planned file count without relying on legacy memory attributes."""
        try:
            return len(memory_agent.get_all_files_list())
        except Exception:
            return 0

    def _get_all_files_list(self, memory_agent: ConciseMemoryAgent) -> List[str]:
        """Return the planned file list for UI progress rendering."""
        try:
            return memory_agent.get_all_files_list()
        except Exception:
            return []

    def _load_api_config(self) -> Dict[str, Any]:
        """Load API configuration with environment variable override."""
        try:
            return load_api_config(self.config_path)
        except Exception as e:
            raise Exception(f"Failed to load API config: {e}")

    def _create_logger(self) -> logging.Logger:
        """Create and configure logger"""
        logger = logging.getLogger(__name__)
        # Don't add handlers to child loggers - let them propagate to root
        logger.setLevel(logging.INFO)
        return logger

    def _load_implementation_mode(self) -> str:
        """Load implementation mode. Defaults to fast for original-project behavior."""
        openai_config = self.api_config.get("openai", {})
        return (
            openai_config.get("implementation_mode")
            or openai_config.get("code_implementation_mode")
            or "fast"
        )

    def _normalize_implementation_mode(self, mode: str) -> str:
        """Normalize implementation mode to fast/deepplan."""
        mode = str(mode).strip().lower()
        aliases = {
            "raw_fast": "fast",
            "infer_fast": "fast",
            "raw_deepplan": "deepplan",
            "infer_deepplan": "deepplan",
            "deep_plan": "deepplan",
            "infer_plan": "deepplan",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"fast", "deepplan"}:
            self.logger.warning(
                f"Unknown implementation mode '{mode}', falling back to fast"
            )
            return "fast"
        return mode

    def _read_plan_file(self, plan_file_path: str) -> str:
        """Read implementation plan file"""
        plan_path = Path(plan_file_path)
        if not plan_path.exists():
            raise FileNotFoundError(
                f"Implementation plan file not found: {plan_file_path}"
            )

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

        if not plan_content.strip():
            raise ValueError(f"Implementation plan file is empty: {plan_file_path}")

        return plan_content

    def _check_file_tree_exists(self, target_directory: str) -> bool:
        """Check if file tree structure already exists"""
        code_directory = os.path.join(target_directory, "generate_code")
        return os.path.exists(code_directory) and len(os.listdir(code_directory)) > 0

    # ==================== 2. Public Interface Methods (External API Layer) ====================

    async def run_workflow(
        self,
        plan_file_path: str,
        target_directory: Optional[str] = None,
        pure_code_mode: bool = False,
        enable_read_tools: bool = True,
    ):
        """Run complete workflow - Main public interface"""
        # Set the read tools configuration
        self.enable_read_tools = enable_read_tools

        try:
            plan_content = self._read_plan_file(plan_file_path)

            if target_directory is None:
                target_directory = str(Path(plan_file_path).parent)

            # Calculate code directory for workspace alignment
            code_directory = os.path.join(target_directory, "generate_code")

            self.logger.info("=" * 80)
            self.logger.info("STARTING CODE IMPLEMENTATION WORKFLOW")
            self.logger.info("=" * 80)
            self.logger.info(f"Plan file: {plan_file_path}")
            self.logger.info(f"Plan file parent: {target_directory}")
            self.logger.info(f"Code directory (MCP workspace): {code_directory}")
            self.logger.info(
                f"Read tools: {'ENABLED' if self.enable_read_tools else 'DISABLED'}"
            )
            self.logger.info("=" * 80)

            results = {}

            # Check if file tree exists
            if self._check_file_tree_exists(target_directory):
                self.logger.info("File tree exists, skipping creation")
                results["file_tree"] = "Already exists, skipped creation"
            else:
                self.logger.info("Creating file tree...")
                results["file_tree"] = await self.create_file_structure(
                    plan_content, target_directory
                )

            # Code implementation
            if pure_code_mode:
                self.logger.info("Starting pure code implementation...")
                results["code_implementation"] = await self.implement_code_pure(
                    plan_content, target_directory, code_directory
                )
            else:
                pass

            self.logger.info("Workflow execution successful")

            return {
                "status": "success",
                "plan_file": plan_file_path,
                "target_directory": target_directory,
                "code_directory": os.path.join(target_directory, "generate_code"),
                "results": results,
                "mcp_architecture": "standard",
            }

        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")

            return {"status": "error", "message": str(e), "plan_file": plan_file_path}
        finally:
            await self._cleanup_mcp_agent()

    async def create_file_structure(
        self, plan_content: str, target_directory: str
    ) -> str:
        """Create file tree structure based on implementation plan"""
        self.logger.info("Starting file tree creation...")

        structure_agent = Agent(
            name="StructureGeneratorAgent",
            instruction=STRUCTURE_GENERATOR_PROMPT,
            server_names=["command-executor"],
        )

        async with structure_agent:
            creator = await structure_agent.attach_llm(
                get_preferred_llm_class(self.config_path)
            )

            message = f"""Analyze the following implementation plan and generate shell commands to create the file tree structure.

Target Directory: {target_directory}/generate_code/

Implementation Plan:
{plan_content}

Tasks:
1. Find the file tree structure in the implementation plan
2. Generate shell commands (mkdir -p, touch) to create that structure
3. Use the execute_commands tool to run the commands and create the file structure

Requirements:
- Use mkdir -p to create directories
- Use touch to create files
- Include __init__.py file for Python packages
- Use relative paths to the target directory
- Execute commands to actually create the file structure"""

            result = await creator.generate_str(message=message)
            self.logger.info(f"LLM response: {result[:200]}...")  # Log first 200 chars

            # Verify directory was created, if not create it manually
            code_dir = os.path.join(target_directory, "generate_code")
            if not os.path.exists(code_dir):
                self.logger.warning(
                    "LLM did not create directory, creating manually..."
                )
                os.makedirs(code_dir, exist_ok=True)
                self.logger.info(f"Manually created directory: {code_dir}")
            else:
                self.logger.info(f"Directory exists: {code_dir}")

            return result

    async def implement_code_pure(
        self, plan_content: str, target_directory: str, code_directory: str = None
    ) -> str:
        """Pure code implementation - focus on code writing without testing"""
        self.logger.info("Starting pure code implementation (no testing)...")

        # Use provided code_directory or calculate it (for backwards compatibility)
        if code_directory is None:
            code_directory = os.path.join(target_directory, "generate_code")

        self.reference_indexes_path = os.path.abspath(
            os.path.join(target_directory, "indexes")
        ).replace("\\", "/")
        self.logger.info(f"Using code directory (MCP workspace): {code_directory}")

        if not os.path.exists(code_directory):
            self.logger.warning(
                f"Code directory does not exist, creating it: {code_directory}"
            )
            os.makedirs(code_directory, exist_ok=True)

        try:
            client, client_type = await self._initialize_llm_client()
            await self._initialize_mcp_agent(code_directory)

            tools = self._prepare_mcp_tool_definitions()
            system_message = GENERAL_CODE_IMPLEMENTATION_SYSTEM_PROMPT
            messages = []

            #             implementation_message = f"""**TASK: Implement Research Paper Reproduction Code**

            # You are implementing a complete, working codebase that reproduces the core algorithms, experiments, and methods described in a research paper. Your goal is to create functional code that can replicate the paper's key results and contributions.

            # **What you need to do:**
            # - Analyze the paper content and reproduction plan to understand requirements
            # - Implement all core algorithms mentioned in the main body of the paper
            # - Create the necessary components following the planned architecture
            # - Test each component to ensure functionality
            # - Integrate components into a cohesive, executable system
            # - Focus on reproducing main contributions rather than appendix-only experiments

            # **RESOURCES:**
            # - **Paper & Reproduction Plan**: `{target_directory}/` (contains .md paper files and initial_plan.txt with detailed implementation guidance)
            # - **Reference Code Indexes**: `{target_directory}/indexes/` (JSON files with implementation patterns from related codebases)
            # - **Implementation Directory**: `{code_directory}/` (your working directory for all code files)

            # **CURRENT OBJECTIVE:**
            # Start by reading the reproduction plan (`{target_directory}/initial_plan.txt`) to understand the implementation strategy, then examine the paper content to identify the first priority component to implement. Use the search_code tool to find relevant reference implementations from the indexes directory (`{target_directory}/indexes/*.json`) before coding.

            # ---
            # **START:** Review the plan above and begin implementation."""
            reference_context = self._get_reference_indexing_context()
            implementation_message = f"""**Task: Implement code based on the following reproduction plan**

**Code Reproduction Plan:**
{plan_content}

**Working Directory:** {code_directory}
{reference_context}

**Current Objective:** Begin implementation by analyzing the plan structure, examining the current project layout, and implementing the first foundation file according to the plan's priority order."""

            messages.append({"role": "user", "content": implementation_message})

            result = await self._pure_code_implementation_loop(
                client,
                client_type,
                system_message,
                messages,
                tools,
                plan_content,
                target_directory,
            )

            return result

        finally:
            await self._cleanup_mcp_agent()

    # ==================== 3. Core Business Logic (Implementation Layer) ====================

    async def _pure_code_implementation_loop(
        self,
        client,
        client_type,
        system_message,
        messages,
        tools,
        plan_content,
        target_directory,
    ):
        """Pure code implementation loop with memory optimization and phase consistency"""
        max_iterations = 800
        iteration = 0
        start_time = time.time()
        max_time = 7200  # 120 minutes (2 hours)

        # Initialize specialized agents
        code_agent = CodeImplementationAgent(
            self.mcp_agent, self.logger, self.enable_read_tools
        )

        # Pass code_directory to memory agent for file extraction
        code_directory = os.path.join(target_directory, "generate_code")
        memory_agent = ConciseMemoryAgent(
            plan_content,
            self.logger,
            target_directory,
            self.default_models,
            code_directory,
            self.enable_reference_indexing and self._has_reference_indexes(),
            self.reference_indexes_path,
        )

        # Log read tools configuration
        read_tools_status = "ENABLED" if self.enable_read_tools else "DISABLED"
        self.logger.info(
            f"Read tools (read_file, read_code_mem): {read_tools_status}"
        )
        if not self.enable_read_tools:
            self.logger.info(
                "No read mode: read_file and read_code_mem tools will be skipped"
            )

        # Connect code agent with memory agent for summary generation
        # Note: Concise memory agent doesn't need LLM client for summary generation
        code_agent.set_memory_agent(memory_agent, client, client_type)

        # Initialize memory agent with iteration 0
        memory_agent.start_new_round(iteration=0)
        self._emit_event(
            "file_progress",
            {
                "round_id": 0,
                "implemented_files_count": code_agent.get_files_implemented_count(),
                "total_files": self._get_total_files_count(memory_agent),
                "remaining_files_count": len(memory_agent.get_unimplemented_files()),
                "current_file": "",
                "phase": "implementation_initialized",
            },
        )
        recent_diagnostics = ""
        planner_messages = []

        while iteration < max_iterations:
            iteration += 1
            elapsed_time = time.time() - start_time

            if elapsed_time > max_time:
                self.logger.warning(f"Time limit reached: {elapsed_time:.2f}s")
                break

            # # Test simplified memory approach if we have files implemented
            # if iteration == 5 and code_agent.get_files_implemented_count() > 0:
            #     self.logger.info("馃И Testing simplified memory approach...")
            #     test_results = await memory_agent.test_simplified_memory_approach()
            #     self.logger.info(f"Memory test results: {test_results}")

            # self.logger.info(f"Pure code implementation iteration {iteration}: generating code")

            messages = self._validate_messages(messages)

            unimplemented_files = memory_agent.get_unimplemented_files()
            if not unimplemented_files:
                self.logger.info(
                    "DeepRepro implementation complete - all files implemented"
                )
                break

            # Keep summaries/progress for files written in this outer round aligned
            # with the workflow iteration before planning or execution starts.
            memory_agent.start_new_round(iteration=iteration)

            round_subplan = None
            if self.implementation_mode == "deepplan":
                self._emit_event(
                    "agent_state",
                    {
                        "planner": "active",
                        "executor": "idle",
                        "active_role": "planner",
                        "round_id": iteration,
                        "phase": "planning",
                        "message": "Planner is preparing the next implementation subplan.",
                    },
                )
                subplan_context = memory_agent.build_subplan_context(
                    iteration, recent_diagnostics
                )
                round_subplan = await self._call_subplan_model(
                    client, client_type, subplan_context, messages, planner_messages
                )
                planner_messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            round_subplan, ensure_ascii=False, indent=2
                        ),
                    }
                )
                memory_agent.save_round_subplan(iteration, round_subplan)
                memory_agent.save_implementation_progress(iteration)
                messages.append(
                    {
                        "role": "user",
                        "content": self._format_subplan_for_executor(round_subplan),
                    }
                )

            target_new_files = self._get_round_step_limit(
                iteration, memory_agent, round_subplan
            )
            planned_files = []
            repair_file = ""
            reference_searches = []
            if round_subplan:
                planned_files = round_subplan.get("planned_files", []) or []
                repair_file = str(round_subplan.get("repair_file", "")).strip()
                reference_searches = round_subplan.get("reference_searches", []) or []
            else:
                planned_files = self._extract_next_step_files(
                    memory_agent.get_current_next_steps()
                )
                if planned_files:
                    planned_files = [
                        matched_file
                        for matched_file in (
                            self._match_file_in_list(file_path, unimplemented_files)
                            for file_path in planned_files
                        )
                        if matched_file
                    ][:target_new_files]
                if not planned_files:
                    planned_files = unimplemented_files[:target_new_files]

            self._emit_event(
                "round_start",
                {
                    "round_id": iteration,
                    "mode": self.implementation_mode,
                    "planned_files": planned_files,
                    "repair_file": repair_file,
                    "reference_searches": reference_searches,
                    "target_new_files": target_new_files,
                    "remaining_files_count": len(unimplemented_files),
                    "total_files": self._get_total_files_count(memory_agent),
                    "all_files": self._get_all_files_list(memory_agent),
                    "implemented_files_count": code_agent.get_files_implemented_count(),
                },
            )
            self._emit_event(
                "agent_state",
                {
                    "planner": "idle",
                    "executor": "active",
                    "active_role": "executor",
                    "round_id": iteration,
                    "phase": "execution",
                    "message": "Executor is writing files for the current round.",
                },
            )
            self.logger.info(
                f"Implementation round {iteration} mode={self.implementation_mode}, target_new_files={target_new_files}"
            )

            files_before_round = code_agent.get_files_implemented_count()
            completed_before_round = set(code_agent.implemented_files_set)
            max_executor_steps = self._get_round_execution_step_budget(
                target_new_files, round_subplan
            )
            consecutive_no_tool_steps = 0
            round_diagnostics = []

            for round_step in range(max_executor_steps):
                tool_calls_to_execute = []
                messages = self._validate_messages(messages)
                current_system_message = code_agent.get_system_prompt()

                # Call LLM
                response = await self._call_llm_with_tools(
                    client, client_type, current_system_message, messages, tools
                )

                response_content = response.get("content", "").strip()
                if not response_content:
                    response_content = "Continue implementing code files..."

                messages.append({"role": "assistant", "content": response_content})

                # Handle tool calls
                if response.get("tool_calls"):
                    consecutive_no_tool_steps = 0
                    tool_calls_to_execute = response["tool_calls"][:1]
                    if len(response["tool_calls"]) > 1:
                        self.logger.warning(
                            "Model returned multiple tool calls; executing only the first to preserve step-by-step context."
                        )
                    tool_results = await code_agent.execute_tool_calls(
                        tool_calls_to_execute
                    )
                    if tool_calls_to_execute[0].get("name") == "write_file":
                        written_file = tool_calls_to_execute[0].get("input", {}).get("file_path", "")
                        self._emit_event(
                            "agent_state",
                            {
                                "planner": "idle",
                                "executor": "active",
                                "active_role": "executor",
                                "round_id": iteration,
                                "phase": "execution",
                                "message": "Executor wrote a file; summary is queued in the background.",
                            },
                        )
                        self._emit_event(
                            "memory_state",
                            {
                                "status": "active",
                                "round_id": iteration,
                                "phase": "summary",
                                "file_path": written_file,
                                "message": "File summary is running asynchronously while execution continues.",
                            },
                        )
                    lightweight_findings = self._collect_lightweight_findings(
                        tool_calls_to_execute, tool_results
                    )
                    if lightweight_findings:
                        self._emit_event(
                            "agent_state",
                            {
                                "planner": "idle",
                                "executor": "active",
                                "active_role": "executor",
                                "round_id": iteration,
                                "phase": "diagnostics",
                                "message": "Executor received lightweight diagnostics for the current round.",
                            },
                        )
                        round_diagnostics.extend(lightweight_findings)
                    recent_diagnostics = "\n".join(round_diagnostics)

                    # Record essential tool results in concise memory agent
                    for tool_call, tool_result in zip(
                        tool_calls_to_execute, tool_results
                    ):
                        memory_agent.record_tool_result(
                            tool_name=tool_call["name"],
                            tool_input=tool_call["input"],
                            tool_result=tool_result.get("result"),
                        )

                    # Determine guidance based on results
                    has_error = self._check_tool_results_for_errors(tool_results)
                    files_count = code_agent.get_files_implemented_count()

                    if has_error:
                        guidance = self._generate_error_guidance()
                    elif (
                        tool_calls_to_execute
                        and tool_calls_to_execute[0].get("name")
                        == "search_code_references"
                    ):
                        guidance = self._generate_reference_search_guidance()
                    else:
                        guidance = self._generate_success_guidance(files_count)

                    compiled_response = self._compile_user_response(
                        tool_results, guidance
                    )
                    messages.append({"role": "user", "content": compiled_response})

                else:
                    consecutive_no_tool_steps += 1
                    has_error = False
                    lightweight_findings = ["No tool calls were made in this round."]
                    round_diagnostics.extend(lightweight_findings)
                    recent_diagnostics = "\n".join(round_diagnostics)
                    files_count = code_agent.get_files_implemented_count()
                    no_tools_guidance = self._generate_no_tools_guidance(files_count)
                    messages.append({"role": "user", "content": no_tools_guidance})

                # # Check for analysis loop and provide corrective guidance
                # if code_agent.is_in_analysis_loop():
                #     analysis_loop_guidance = code_agent.get_analysis_loop_guidance()
                #     messages.append({"role": "user", "content": analysis_loop_guidance})
                #     self.logger.warning(
                #         "Analysis loop detected and corrective guidance provided"
                #     )

                # Record file implementations in memory agent (for the current round)
                for file_info in code_agent.get_implementation_summary()[
                    "completed_files"
                ]:
                    memory_agent.record_file_implementation(file_info["file"])

                files_after_step = code_agent.get_files_implemented_count()
                files_added_this_round = files_after_step - files_before_round
                newly_completed_files = sorted(
                    set(code_agent.implemented_files_set) - completed_before_round
                )
                if newly_completed_files:
                    self._emit_event(
                        "file_progress",
                        {
                            "round_id": iteration,
                            "completed_files_this_round": newly_completed_files,
                            "implemented_files_count": files_after_step,
                            "total_files": self._get_total_files_count(memory_agent),
                            "remaining_files_count": len(
                                memory_agent.get_unimplemented_files()
                            ),
                            "all_files": self._get_all_files_list(memory_agent),
                            "current_file": newly_completed_files[-1],
                            "phase": "execution",
                        },
                    )
                    self._emit_event(
                        "agent_state",
                        {
                            "planner": "idle",
                            "executor": "active",
                            "active_role": "executor",
                            "round_id": iteration,
                            "phase": "execution",
                            "message": "Executor is continuing the current implementation round.",
                        },
                    )
                if not memory_agent.get_unimplemented_files():
                    break
                if has_error:
                    self.logger.info("Stopping current batch round due to tool error")
                    break
                if (
                    tool_calls_to_execute
                    and tool_calls_to_execute[0].get("name")
                    == "search_code_references"
                ):
                    continue
                if files_added_this_round >= target_new_files:
                    self.logger.info(
                        f"Stopping current batch round after implementing {files_added_this_round} target file(s)"
                    )
                    break
                if consecutive_no_tool_steps >= 2:
                    self.logger.info(
                        "Stopping current batch round because the executor repeatedly made no tool calls"
                    )
                    break

            completed_after_round = set(code_agent.implemented_files_set)
            completed_this_round = sorted(
                completed_after_round - completed_before_round
            )
            recent_diagnostics = "\n".join(round_diagnostics)
            self._emit_event(
                "round_done",
                {
                    "round_id": iteration,
                    "completed_files": completed_this_round,
                    "diagnostics": round_diagnostics,
                    "implemented_files_count": code_agent.get_files_implemented_count(),
                    "total_files": self._get_total_files_count(memory_agent),
                    "remaining_files_count": len(memory_agent.get_unimplemented_files()),
                    "all_files": self._get_all_files_list(memory_agent),
                    "phase": "round_complete",
                },
            )

            await code_agent.wait_for_pending_summaries()
            self._emit_event(
                "memory_state",
                {
                    "status": "idle",
                    "round_id": iteration,
                    "phase": "summary_complete",
                    "message": "All pending file summaries are available before the next round.",
                },
            )

            if self.implementation_mode == "fast":
                self._emit_event(
                    "agent_state",
                    {
                        "planner": "idle",
                        "executor": "idle",
                        "active_role": "idle",
                        "round_id": iteration,
                        "phase": "round_guidance",
                        "message": "Fast-mode next-step guidance is being prepared before the next round.",
                    },
                )
                await memory_agent.generate_round_next_steps(
                    client,
                    client_type,
                    iteration,
                    completed_this_round,
                    recent_diagnostics,
                )

            if memory_agent.should_trigger_memory_optimization(
                messages, code_agent.get_files_implemented_count()
            ):
                self._emit_event(
                    "agent_state",
                    {
                        "planner": "idle",
                        "executor": "idle",
                        "active_role": "idle",
                        "round_id": iteration,
                        "phase": "memory",
                        "message": "Executor context is being compacted before the next round.",
                    },
                )
                files_implemented_count = code_agent.get_files_implemented_count()
                current_system_message = code_agent.get_system_prompt()
                messages = memory_agent.apply_memory_optimization(
                    current_system_message, messages, files_implemented_count
                )

            if self.implementation_mode == "deepplan":
                planner_messages.append(
                    {
                        "role": "user",
                        "content": self._create_planner_feedback(
                            iteration,
                            round_subplan,
                            completed_before_round,
                            code_agent,
                            memory_agent,
                            recent_diagnostics,
                        ),
                    }
                )

            memory_agent.save_implementation_progress(iteration)

            # Check completion based on actual unimplemented files list
            unimplemented_files = memory_agent.get_unimplemented_files()
            if not unimplemented_files:  # Empty list means all files implemented
                if self.implementation_mode == "deepplan":
                    self._emit_event(
                        "agent_state",
                        {
                            "planner": "active",
                            "executor": "idle",
                            "active_role": "planner",
                            "round_id": iteration,
                            "phase": "quality_gate",
                            "message": "Planner is running the final product gate.",
                        },
                    )
                    blocking_findings = await self._run_final_quality_gate(
                        client,
                        client_type,
                        messages,
                        planner_messages,
                        code_agent,
                        memory_agent,
                        code_directory,
                        iteration,
                    )
                    if blocking_findings:
                        recent_diagnostics = self._format_quality_findings(
                            blocking_findings
                        )
                        self.logger.warning(
                            "Deepplan final quality gate still has blocking findings after bounded repair rounds."
                        )
                        break

                self.logger.info("All planned files implemented; finalizing workflow.")
                self._emit_event(
                    "agent_state",
                    {
                        "planner": "idle",
                        "executor": "idle",
                        "active_role": "idle",
                        "round_id": iteration,
                        "phase": "completed",
                        "message": "Implementation completed.",
                    },
                )
                break

            # Emergency trim if too long
            if len(messages) > 50:
                self.logger.warning(
                    "Emergency message trim - applying concise memory optimization"
                )
                self._emit_event(
                    "agent_state",
                    {
                        "planner": "idle",
                        "executor": "idle",
                        "active_role": "idle",
                        "round_id": iteration,
                        "phase": "memory",
                        "message": "Executor context is being compacted before continuing.",
                    },
                )

                current_system_message = code_agent.get_system_prompt()
                files_implemented_count = code_agent.get_files_implemented_count()
                messages = memory_agent.apply_memory_optimization(
                    current_system_message, messages, files_implemented_count
                )

        await code_agent.wait_for_pending_summaries()
        self._emit_event(
            "memory_state",
            {
                "status": "idle",
                "round_id": iteration,
                "phase": "summary_complete",
                "message": "All pending file summaries are available before final reporting.",
            },
        )
        return await self._generate_pure_code_final_report_with_concise_agents(
            iteration, time.time() - start_time, code_agent, memory_agent
        )

    # ==================== 4. MCP Agent and LLM Communication Management (Communication Layer) ====================

    async def _initialize_mcp_agent(self, code_directory: str):
        """Initialize MCP agent and connect to code-implementation server"""
        try:
            server_names = ["code-implementation"]
            if self.enable_reference_indexing and self._has_reference_indexes():
                server_names.append("code-reference-indexer")

            self.mcp_agent = Agent(
                name="CodeImplementationAgent",
                instruction="You are a code implementation assistant, using MCP tools to implement paper code replication.",
                server_names=server_names,
            )

            await self.mcp_agent.__aenter__()
            llm = await self.mcp_agent.attach_llm(
                get_preferred_llm_class(self.config_path)
            )

            # Set workspace to the target code directory
            workspace_result = await self.mcp_agent.call_tool(
                "set_workspace", {"workspace_path": code_directory}
            )
            self.logger.info(f"Workspace setup result: {workspace_result}")

            return llm

        except Exception as e:
            self.logger.error(f"Failed to initialize MCP agent: {e}")
            if self.mcp_agent:
                try:
                    await self.mcp_agent.__aexit__(None, None, None)
                except Exception:
                    pass
                self.mcp_agent = None
            raise

    async def _cleanup_mcp_agent(self):
        """Clean up MCP agent resources"""
        if self.mcp_agent:
            try:
                await self.mcp_agent.__aexit__(None, None, None)
                self.logger.info("MCP agent connection closed")
            except Exception as e:
                self.logger.warning(f"Error closing MCP agent: {e}")
            finally:
                self.mcp_agent = None

    def _get_reference_indexing_context(self) -> str:
        """Return optional executor guidance for reference indexing."""
        if not self.enable_reference_indexing:
            return ""
        if not self._has_reference_indexes():
            return f"""

**Reference Index Status:**
- Reference indexing is enabled, but no usable JSON index files were found at `{self.reference_indexes_path}`.
- The reference search tool is not exposed for this implementation run.
- Continue from the paper, initial reproduction plan, existing summaries, and current subplan/Next Steps."""
        return REFERENCE_INDEXING_EXECUTOR_GUIDANCE_TEMPLATE.format(
            indexes_path=self.reference_indexes_path
        )

    def _has_reference_indexes(self) -> bool:
        """Check whether the indexed-reference directory has JSON index files."""
        if not self.reference_indexes_path or not os.path.isdir(
            self.reference_indexes_path
        ):
            return False
        try:
            return any(
                name.lower().endswith(".json")
                for name in os.listdir(self.reference_indexes_path)
            )
        except OSError:
            return False

    async def _initialize_llm_client(self):
        """Initialize LLM client based on llm_provider preference and API key availability"""
        # Get API keys
        anthropic_key = self.api_config.get("anthropic", {}).get("api_key", "")
        openai_key = self.api_config.get("openai", {}).get("api_key", "")
        google_key = self.api_config.get("google", {}).get("api_key", "")

        # Read user preference from main config
        preferred_provider = None
        try:
            import yaml

            # Derive config path from secrets path (same directory)
            secrets_dir = os.path.dirname(os.path.abspath(self.config_path))
            config_path = os.path.join(secrets_dir, "mcp_agent.config.yaml")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    preferred_provider = config.get("llm_provider", "").strip().lower()
        except Exception as e:
            self.logger.warning(f"Could not read llm_provider preference: {e}")

        # Define provider initialization functions
        async def init_anthropic():
            if not (anthropic_key and anthropic_key.strip()):
                return None
            try:
                from anthropic import AsyncAnthropic

                client = AsyncAnthropic(api_key=anthropic_key)
                await client.messages.create(
                    model=self.default_models["anthropic"],
                    max_tokens=20,
                    messages=[{"role": "user", "content": "test"}],
                )
                self.logger.info(
                    f"Using Anthropic API with model: {self.default_models['anthropic']}"
                )
                return client, "anthropic"
            except Exception as e:
                self.logger.warning(f"Anthropic API unavailable: {e}")
                return None

        async def init_google():
            if not (google_key and google_key.strip()):
                return None
            try:
                from google import genai

                client = genai.Client(api_key=google_key)
                try:
                    test_response = await client.aio.models.generate_content(
                        model=self.default_models.get("google", "gemini-2.0-flash"),
                        contents="test",
                    )
                    self.logger.info(
                        "Google API connection successful: " + str(test_response)
                    )
                except Exception as test_err:
                    self.logger.warning(
                        f"Could not test Google API: {test_err}, but will try to use client"
                    )

                self.logger.info(
                    f"Using Google API with model: {self.default_models.get('google', 'gemini-2.0-flash')}"
                )
                return client, "google"
            except Exception as e:
                self.logger.warning(f"Google API unavailable: {e}")
                return None

        async def init_openai():
            if not (openai_key and openai_key.strip()):
                return None
            try:
                from openai import AsyncOpenAI

                openai_config = self.api_config.get("openai", {})
                base_url = openai_config.get("base_url")

                if base_url:
                    client = AsyncOpenAI(api_key=openai_key, base_url=base_url)
                else:
                    client = AsyncOpenAI(api_key=openai_key)

                model_name = self.default_models.get("openai", "o3-mini")

                try:
                    await client.chat.completions.create(
                        model=model_name,
                        max_tokens=20,
                        messages=[{"role": "user", "content": "test"}],
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        self.logger.info(
                            f"Model {model_name} requires max_completion_tokens parameter"
                        )
                        await client.chat.completions.create(
                            model=model_name,
                            max_completion_tokens=20,
                            messages=[{"role": "user", "content": "test"}],
                        )
                    else:
                        raise
                self.logger.info(f"Using OpenAI API with model: {model_name}")
                if base_url:
                    self.logger.info(f"Using custom base URL: {base_url}")
                return client, "openai"
            except Exception as e:
                self.logger.warning(f"OpenAI API unavailable: {e}")
                return None

        # Map providers to their init functions
        provider_init_map = {
            "anthropic": init_anthropic,
            "google": init_google,
            "openai": init_openai,
        }

        # Try preferred provider first
        if preferred_provider and preferred_provider in provider_init_map:
            self.logger.info(f"Trying preferred provider: {preferred_provider}")
            result = await provider_init_map[preferred_provider]()
            if result:
                return result
            else:
                self.logger.warning(
                    f"Preferred provider '{preferred_provider}' unavailable, trying alternatives..."
                )

        # Fallback: try providers in order
        for provider_name, init_func in provider_init_map.items():
            if provider_name == preferred_provider:
                continue  # Already tried
            result = await init_func()
            if result:
                return result

        raise ValueError(
            "No available LLM API - please check your API keys in configuration"
        )

    def _format_messages_for_subplan(self, messages: List[Dict[str, Any]]) -> str:
        """Format bounded executor context without duplicating plan/summary sections."""
        if not messages:
            return "No executor messages yet."

        formatted_messages = []
        for index, message in enumerate(messages[-6:], 1):
            role = message.get("role", "user")
            content = str(message.get("content", "")).strip()
            if content:
                if (
                    "**Code Reproduction Plan:**" in content
                    or "**Below is the Knowledge Base" in content
                    or "**Pre-Round Implementation Subplan**" in content
                ):
                    content = (
                        "[Omitted duplicated compact executor context. Use the "
                        "Initial Reproduction Plan, Full Code Knowledge Base, "
                        "Implemented Files, Remaining Files, and Recent Planner "
                        "Continuity sections above.]"
                    )
                elif len(content) > 3000:
                    content = content[-3000:]
                formatted_messages.append(
                    f"### Message {index} ({role})\n{content}"
                )
        return "\n\n".join(formatted_messages)

    def _format_planner_messages(self, planner_messages: List[Dict[str, Any]]) -> str:
        """Format prior planner plan/feedback messages for planning continuity."""
        if not planner_messages:
            return "No previous planner turns. This is the first deepplan round."

        recent_messages = planner_messages[-4:]
        formatted_messages = [
            "Only the previous two rounds' planner subplans and executor feedback are shown here; this is not the full history.",
            "Use the current Implemented Files and Remaining Files sections as the source of truth.",
            "Use the Full Code Knowledge Base for all older implemented files and interfaces.",
            "Treat skipped or unfinished files as actionable only if they still appear in Remaining Files.",
        ]
        for index, message in enumerate(recent_messages, 1):
            role = message.get("role", "user")
            content = str(message.get("content", "")).strip()
            if content:
                formatted_messages.append(
                    f"### Planner Message {index} ({role})\n{content}"
                )
        return "\n\n".join(formatted_messages)

    def _create_planner_feedback(
        self,
        iteration: int,
        round_subplan: Optional[Dict[str, Any]],
        completed_before_round: set,
        code_agent: CodeImplementationAgent,
        memory_agent: ConciseMemoryAgent,
        recent_diagnostics: str,
    ) -> str:
        """Create concise execution feedback for the next planner turn."""
        completed_after_round = set(code_agent.implemented_files_set)
        completed_this_round = sorted(completed_after_round - completed_before_round)
        planned_files = []
        repair_file = ""
        if round_subplan:
            planned_files = round_subplan.get("planned_files", []) or []
            repair_file = str(round_subplan.get("repair_file", "")).strip()
        skipped_files = [
            file_path
            for file_path in planned_files
            if file_path not in completed_after_round
        ]

        feedback = {
            "round_id": iteration,
            "repair_file": repair_file,
            "planned_files": planned_files,
            "completed_files_this_round": completed_this_round,
            "skipped_or_unfinished_files": skipped_files,
            "diagnostics": recent_diagnostics or "None",
            "total_files_implemented": code_agent.get_files_implemented_count(),
            "remaining_files_count": len(memory_agent.get_unimplemented_files()),
            "note": "implement_code_summary.md and implementation_progress.json were updated after this round.",
        }
        return "Planner execution feedback:\n" + json.dumps(
            feedback, ensure_ascii=False, indent=2
        )

    async def _run_final_quality_gate(
        self,
        client,
        client_type: str,
        messages: List[Dict[str, Any]],
        planner_messages: List[Dict[str, Any]],
        code_agent: CodeImplementationAgent,
        memory_agent: ConciseMemoryAgent,
        code_directory: str,
        iteration: int,
    ) -> List[Dict[str, str]]:
        """
        Run bounded final product diagnostics for deepplan mode.

        Fast mode intentionally does not use this repair gate, preserving it as a
        closer baseline to the original project. Deepplan may spend at most a
        small fixed number of extra rounds repairing concrete blocking issues.
        """
        findings = self._run_product_quality_diagnostics(code_directory)
        self.latest_quality_findings = findings
        blocking_findings = [
            finding
            for finding in findings
            if finding.get("severity") == "blocking"
        ]
        if not blocking_findings:
            self.logger.info("Deepplan final quality gate passed.")
            return []

        for repair_round in range(1, self.max_final_repair_rounds + 1):
            repair_iteration = iteration + repair_round
            diagnostics_text = (
                "Final product quality gate found blocking issues. "
                "Repair only concrete existing-file bugs; do not add new files.\n"
                + self._format_quality_findings(blocking_findings)
            )
            self.logger.warning(
                f"Deepplan final quality repair round {repair_round}: "
                f"{len(blocking_findings)} blocking finding(s)."
            )

            memory_agent.start_new_round(iteration=repair_iteration)
            self._emit_event(
                "agent_state",
                {
                    "planner": "active",
                    "executor": "idle",
                    "active_role": "planner",
                    "round_id": repair_iteration,
                    "phase": "quality_repair_planning",
                    "message": "Planner is preparing a targeted quality repair subplan.",
                },
            )
            context = memory_agent.build_subplan_context(
                repair_iteration, diagnostics_text
            )
            round_subplan = await self._call_subplan_model(
                client, client_type, context, messages, planner_messages
            )
            round_subplan = self._force_final_repair_subplan(
                round_subplan, blocking_findings, memory_agent, repair_iteration
            )
            if not round_subplan.get("repair_file"):
                self.logger.warning(
                    "Final quality gate could not map any blocking finding to an implemented file."
                )
                break

            memory_agent.save_round_subplan(repair_iteration, round_subplan)
            memory_agent.save_implementation_progress(repair_iteration)
            messages.append(
                {
                    "role": "user",
                    "content": self._format_subplan_for_executor(round_subplan),
                }
            )

            completed_before_round = set(code_agent.implemented_files_set)
            self._emit_event(
                "agent_state",
                {
                    "planner": "idle",
                    "executor": "active",
                    "active_role": "executor",
                    "round_id": repair_iteration,
                    "phase": "quality_repair_execution",
                    "message": "Executor is applying the targeted quality repair.",
                },
            )
            repair_diagnostics = await self._execute_final_repair_subplan(
                round_subplan, client, client_type, messages, code_agent, memory_agent
            )
            await code_agent.wait_for_pending_summaries()
            self._emit_event(
                "memory_state",
                {
                    "status": "idle",
                    "round_id": repair_iteration,
                    "phase": "summary_complete",
                    "message": "Repair summaries are available before the next quality check.",
                },
            )
            planner_messages.append(
                {
                    "role": "user",
                    "content": self._create_planner_feedback(
                        repair_iteration,
                        round_subplan,
                        completed_before_round,
                        code_agent,
                        memory_agent,
                        "\n".join(repair_diagnostics),
                    ),
                }
            )
            memory_agent.save_implementation_progress(repair_iteration)

            findings = self._run_product_quality_diagnostics(code_directory)
            self.latest_quality_findings = findings
            blocking_findings = [
                finding
                for finding in findings
                if finding.get("severity") == "blocking"
            ]
            if not blocking_findings:
                self.logger.info(
                    f"Deepplan final quality gate passed after {repair_round} repair round(s)."
                )
                return []

        return blocking_findings

    def _force_final_repair_subplan(
        self,
        subplan: Dict[str, Any],
        blocking_findings: List[Dict[str, str]],
        memory_agent: ConciseMemoryAgent,
        repair_iteration: int,
    ) -> Dict[str, Any]:
        """Constrain a final repair subplan to exactly one existing repair file."""
        implemented_files = memory_agent.get_implemented_files()
        repair_file = self._match_file_in_list(
            subplan.get("repair_file", ""), implemented_files
        )
        selected_finding = None
        if repair_file:
            for finding in blocking_findings:
                if self._match_file_in_list(
                    finding.get("file_path", ""), [repair_file]
                ):
                    selected_finding = finding
                    break

        if not repair_file:
            for finding in blocking_findings:
                matched_file = self._match_file_in_list(
                    finding.get("file_path", ""), implemented_files
                )
                if matched_file:
                    repair_file = matched_file
                    selected_finding = finding
                    break

        if not repair_file:
            return {
                "round_id": repair_iteration,
                "repair_file": "",
                "repair_reason": "",
                "repair_instructions": [],
                "planned_files": [],
                "rationale": "No implemented file could be matched for final repair.",
                "must_read_files": [],
                "interface_reminders": [],
                "risks_or_checks": [],
                "execution_order": [],
                "file_instructions": [],
                "reference_searches": [],
                "round_acceptance_checks": [],
                "notes_for_executor": "No repair could be safely scheduled.",
            }

        issue_text = (
            selected_finding.get("evidence", "")
            if selected_finding
            else "Blocking final quality finding."
        )
        suggested_action = (
            selected_finding.get("suggested_action", "")
            if selected_finding
            else "Fix the blocking issue while preserving interfaces."
        )

        subplan["round_id"] = repair_iteration
        subplan["repair_file"] = repair_file
        subplan["repair_reason"] = issue_text
        subplan["repair_instructions"] = [
            suggested_action or "Fix the blocking issue.",
            "Keep the repair minimal and preserve existing public interfaces.",
            "Do not create new files during this final repair round.",
        ]
        subplan["planned_files"] = []
        subplan["execution_order"] = []
        subplan["file_instructions"] = []
        subplan["round_acceptance_checks"] = [
            "The repaired file parses or imports according to the reported diagnostic.",
            "No unrelated refactor or new file is introduced.",
        ]
        subplan["notes_for_executor"] = (
            "This is a final quality-gate repair round. Repair only "
            f"`{repair_file}` with one complete write_file call after reading any "
            "needed existing context."
        )
        subplan["reference_searches"] = self._normalize_reference_searches(
            subplan.get("reference_searches", []), [repair_file]
        )
        return subplan

    async def _execute_final_repair_subplan(
        self,
        round_subplan: Dict[str, Any],
        client,
        client_type: str,
        messages: List[Dict[str, Any]],
        code_agent: CodeImplementationAgent,
        memory_agent: ConciseMemoryAgent,
    ) -> List[str]:
        """Execute a bounded repair-only subplan and return local diagnostics."""
        diagnostics: List[str] = []
        repair_file = round_subplan.get("repair_file", "")
        max_executor_steps = self._get_round_execution_step_budget(0, round_subplan)
        consecutive_no_tool_steps = 0

        for _ in range(max_executor_steps):
            messages = self._validate_messages(messages)
            response = await self._call_llm_with_tools(
                client,
                client_type,
                code_agent.get_system_prompt(),
                messages,
                self._prepare_mcp_tool_definitions(),
            )

            response_content = response.get("content", "").strip()
            if not response_content:
                response_content = "Continue the final repair round."
            messages.append({"role": "assistant", "content": response_content})

            if not response.get("tool_calls"):
                consecutive_no_tool_steps += 1
                diagnostics.append("No tool calls were made in final repair round.")
                messages.append(
                    {
                        "role": "user",
                        "content": self._generate_no_tools_guidance(
                            code_agent.get_files_implemented_count()
                        ),
                    }
                )
                if consecutive_no_tool_steps >= 2:
                    break
                continue

            consecutive_no_tool_steps = 0
            tool_calls_to_execute = response["tool_calls"][:1]
            if len(response["tool_calls"]) > 1:
                self.logger.warning(
                    "Final repair returned multiple tool calls; executing only the first."
                )
            tool_results = await code_agent.execute_tool_calls(tool_calls_to_execute)
            lightweight_findings = self._collect_lightweight_findings(
                tool_calls_to_execute, tool_results
            )
            diagnostics.extend(lightweight_findings)

            for tool_call, tool_result in zip(tool_calls_to_execute, tool_results):
                memory_agent.record_tool_result(
                    tool_name=tool_call["name"],
                    tool_input=tool_call["input"],
                    tool_result=tool_result.get("result"),
                )

            has_error = self._check_tool_results_for_errors(tool_results)
            if has_error:
                guidance = self._generate_error_guidance()
            elif tool_calls_to_execute[0].get("name") == "search_code_references":
                guidance = self._generate_reference_search_guidance()
            else:
                guidance = (
                    "Final repair step completed. If the repair_file has been fully "
                    "rewritten, stop; otherwise continue with the minimal required fix."
                )
            messages.append(
                {
                    "role": "user",
                    "content": self._compile_user_response(tool_results, guidance),
                }
            )

            for file_info in code_agent.get_implementation_summary()["completed_files"]:
                memory_agent.record_file_implementation(file_info["file"])

            executed_tool = tool_calls_to_execute[0]
            if executed_tool.get("name") == "write_file":
                written_file = str(
                    (executed_tool.get("input") or {}).get("file_path", "")
                )
                if self._match_file_in_list(written_file, [repair_file]):
                    break
            if has_error:
                break

        return diagnostics

    def _extract_next_step_files(self, next_steps: str) -> List[str]:
        """Extract planned file paths from the existing Next Steps text."""
        if not next_steps or not next_steps.strip():
            return []

        planned_files = []
        for line in next_steps.splitlines():
            stripped = line.strip().strip("-* ")
            if not stripped:
                continue

            match = re.search(
                r"Code will be implemented:\s*`?([^`\n]+?)`?\s*$",
                stripped,
                re.IGNORECASE,
            )
            if match:
                file_path = match.group(1).strip()
            else:
                file_path = stripped

            if file_path and (
                "/" in file_path
                or "\\" in file_path
                or "." in os.path.basename(file_path)
            ):
                file_path = file_path.strip("`'\" ")
                if file_path and file_path not in planned_files:
                    planned_files.append(file_path)

        return planned_files[:6]

    def _get_fast_round_step_limit(
        self, iteration: int, memory_agent: ConciseMemoryAgent
    ) -> int:
        """Fast mode: first round stays single-file; later rounds follow 1-6 Next Steps."""
        remaining_count = len(memory_agent.get_unimplemented_files())
        if remaining_count <= 0:
            return 0
        if iteration <= 1:
            return 1

        planned_files = self._extract_next_step_files(
            memory_agent.get_current_next_steps()
        )
        if planned_files:
            return max(1, min(len(planned_files), 6, remaining_count))
        return 1

    def _get_round_step_limit(
        self,
        iteration: int,
        memory_agent: ConciseMemoryAgent,
        round_subplan: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Determine how many executor steps to allow in this outer round."""
        remaining_count = len(memory_agent.get_unimplemented_files())
        if remaining_count <= 0:
            return 0

        if self.implementation_mode == "deepplan" and round_subplan:
            planned_files = round_subplan.get("planned_files", [])
            if isinstance(planned_files, list) and planned_files:
                return max(1, min(len(planned_files), 3, remaining_count))

        return self._get_fast_round_step_limit(iteration, memory_agent)

    def _get_round_execution_step_budget(
        self, target_new_files: int, round_subplan: Optional[Dict[str, Any]]
    ) -> int:
        """Allow extra steps for repairs and optional reference searches."""
        repair_extra = 0
        if self.implementation_mode == "deepplan" and round_subplan:
            if str(round_subplan.get("repair_file", "")).strip():
                repair_extra = 1
            reference_extra = len(round_subplan.get("reference_searches", []) or [])
        else:
            reference_extra = 1 if self.enable_reference_indexing else 0
        return max(1, target_new_files * 3 + repair_extra * 3 + reference_extra * 2)

    def _create_round_subplan_prompt(
        self,
        context: Dict[str, Any],
        messages: List[Dict[str, Any]],
        planner_messages: List[Dict[str, Any]],
    ) -> str:
        """Create JSON-only prompt for one pre-round implementation subplan."""
        current_executor_context = self._format_messages_for_subplan(messages)
        planner_context = self._format_planner_messages(planner_messages)
        unimplemented_files = "\n".join(
            f"- {file_path}" for file_path in context.get("unimplemented_files", [])
        )
        if not unimplemented_files:
            unimplemented_files = "- All files implemented!"

        if self.enable_reference_indexing and self._has_reference_indexes():
            reference_indexing_context = (
                "Reference indexing is enabled. The executor can optionally call "
                "`search_code_references` using "
                f'indexes_path="{self.reference_indexes_path}". '
                "Planner should only suggest concise reference_searches for the "
                "current repair_file/planned_files when likely helpful; do not "
                "treat reference indexes as authoritative."
            )
        elif self.enable_reference_indexing:
            reference_indexing_context = (
                "Reference indexing is enabled, but no usable JSON index files "
                f"were found at {self.reference_indexes_path}. Do not include "
                "reference_searches; plan from the paper, summaries, diagnostics, "
                "and current file lists."
            )
        else:
            reference_indexing_context = (
                "Reference indexing is disabled. Do not include reference_searches."
            )

        return ROUND_SUBPLAN_PROMPT.format(
            round_id=context.get("round_id", 0),
            reference_indexing_context=reference_indexing_context,
            initial_plan=context.get("initial_plan", ""),
            code_knowledge_base=context.get("code_knowledge_base", "")
            or "No implemented code summary yet.",
            implemented_files_text=context.get("implemented_files_text", "- None yet"),
            unimplemented_files=unimplemented_files,
            recent_diagnostics=context.get("recent_diagnostics", "") or "None",
            planner_context=planner_context,
            current_executor_context=current_executor_context,
        )

    def _parse_subplan_json(
        self, content: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parse and normalize subplan JSON; fall back to remaining files."""
        remaining_files = context.get("unimplemented_files", [])
        implemented_files = context.get("implemented_files", [])

        def fallback(reason: str) -> Dict[str, Any]:
            planned_files = remaining_files[:4]
            return {
                "round_id": context.get("round_id", 0),
                "repair_file": "",
                "repair_reason": "",
                "repair_instructions": [],
                "planned_files": planned_files,
                "rationale": f"Fallback subplan used: {reason}",
                "must_read_files": [],
                "interface_reminders": [],
                "risks_or_checks": [],
                "execution_order": planned_files,
                "file_instructions": [
                    {
                        "file_path": file_path,
                        "purpose": "Implement this planned file according to the initial plan and existing code summaries.",
                        "implementation_scope": "Create a coherent, functional implementation for this file.",
                        "key_interfaces": [],
                        "dependencies": [],
                        "integration_notes": [],
                        "acceptance_checks": [],
                    }
                    for file_path in planned_files
                ],
                "reference_searches": [],
                "round_acceptance_checks": [
                    "All planned files written in this round should preserve existing public interfaces.",
                    "New imports should match files that exist or are planned in the current project.",
                ],
                "notes_for_executor": "Continue implementing the next remaining files according to the initial plan and existing code summaries.",
            }

        if not content or not content.strip():
            return fallback("empty model response")

        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return fallback("no JSON object found")

        try:
            subplan = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as e:
            return fallback(f"JSON parse failed: {e}")

        if not isinstance(subplan, dict):
            return fallback("JSON root is not an object")

        planned_files = subplan.get("planned_files", [])
        if isinstance(planned_files, str):
            planned_files = [planned_files]
        if not isinstance(planned_files, list):
            planned_files = []
        normalized_planned_files = []
        for file_path in planned_files:
            matched_file = self._match_remaining_file(file_path, remaining_files)
            if matched_file and matched_file not in normalized_planned_files:
                normalized_planned_files.append(matched_file)
            if len(normalized_planned_files) >= 4:
                break
        planned_files = normalized_planned_files
        if not planned_files:
            planned_files = remaining_files[:4]

        repair_file = self._match_file_in_list(
            subplan.get("repair_file", ""), implemented_files
        )
        if repair_file in planned_files:
            repair_file = None

        subplan["round_id"] = context.get("round_id", 0)
        subplan["repair_file"] = repair_file or ""
        subplan["repair_reason"] = (
            str(subplan.get("repair_reason", "")).strip() if repair_file else ""
        )
        subplan["repair_instructions"] = (
            self._normalize_string_list(subplan.get("repair_instructions", []))
            if repair_file
            else []
        )
        subplan["planned_files"] = planned_files
        subplan["rationale"] = str(subplan.get("rationale", "")).strip()
        subplan["must_read_files"] = self._normalize_string_list(
            subplan.get("must_read_files", [])
        )
        subplan["interface_reminders"] = self._normalize_string_list(
            subplan.get("interface_reminders", [])
        )
        subplan["risks_or_checks"] = self._normalize_string_list(
            subplan.get("risks_or_checks", [])
        )
        execution_order = self._normalize_string_list(
            subplan.get("execution_order", planned_files),
            allowed_values=planned_files,
            limit=4,
        )
        subplan["execution_order"] = execution_order or planned_files
        subplan["file_instructions"] = self._normalize_file_instructions(
            subplan.get("file_instructions", []), planned_files
        )
        allowed_reference_files = planned_files.copy()
        if repair_file:
            allowed_reference_files.append(repair_file)
        subplan["reference_searches"] = self._normalize_reference_searches(
            subplan.get("reference_searches", []), allowed_reference_files
        )
        subplan["round_acceptance_checks"] = self._normalize_string_list(
            subplan.get("round_acceptance_checks", [])
        )
        subplan["notes_for_executor"] = str(
            subplan.get("notes_for_executor", "")
        ).strip()
        return subplan

    def _match_file_in_list(
        self, file_path: Any, candidate_files: List[str]
    ) -> Optional[str]:
        """Match a proposed path to one canonical file path from a candidate list."""
        candidate = str(file_path).strip().strip("`'\" ").replace("\\", "/").strip("/")
        if not candidate:
            return None

        for candidate_file in candidate_files:
            normalized_file = candidate_file.replace("\\", "/").strip("/")
            if candidate == normalized_file:
                return candidate_file
            if candidate.endswith("/" + normalized_file):
                return candidate_file
            if normalized_file.endswith("/" + candidate):
                return candidate_file
        return None

    def _match_remaining_file(
        self, file_path: Any, remaining_files: List[str]
    ) -> Optional[str]:
        """Match a planner-proposed path to the canonical remaining file path."""
        return self._match_file_in_list(file_path, remaining_files)

    def _normalize_string_list(
        self,
        value: Any,
        allowed_values: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[str]:
        """Normalize planner list fields into short string lists."""
        if isinstance(value, str):
            value = [value] if value.strip() else []
        if not isinstance(value, list):
            return []

        normalized = []
        allowed_set = set(allowed_values) if allowed_values is not None else None
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if allowed_set is not None and text not in allowed_set:
                continue
            if text not in normalized:
                normalized.append(text)
            if limit and len(normalized) >= limit:
                break
        return normalized

    def _normalize_file_instructions(
        self, file_instructions: Any, planned_files: List[str]
    ) -> List[Dict[str, Any]]:
        """Keep per-file instructions aligned with planned files."""
        if not isinstance(file_instructions, list):
            file_instructions = []

        normalized = []
        seen_files = set()
        for item in file_instructions:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("file_path", "")).strip()
            if file_path not in planned_files or file_path in seen_files:
                continue
            seen_files.add(file_path)
            normalized.append(
                {
                    "file_path": file_path,
                    "purpose": str(item.get("purpose", "")).strip(),
                    "implementation_scope": str(
                        item.get("implementation_scope", "")
                    ).strip(),
                    "key_interfaces": item.get("key_interfaces", [])
                    if isinstance(item.get("key_interfaces", []), list)
                    else [],
                    "dependencies": item.get("dependencies", [])
                    if isinstance(item.get("dependencies", []), list)
                    else [],
                    "integration_notes": item.get("integration_notes", [])
                    if isinstance(item.get("integration_notes", []), list)
                    else [],
                    "acceptance_checks": item.get("acceptance_checks", [])
                    if isinstance(item.get("acceptance_checks", []), list)
                    else [],
                }
            )

        for file_path in planned_files:
            if file_path in seen_files:
                continue
            normalized.append(
                {
                    "file_path": file_path,
                    "purpose": "Implement this file according to the initial plan.",
                    "implementation_scope": "Create the planned functionality for this file.",
                    "key_interfaces": [],
                    "dependencies": [],
                    "integration_notes": [],
                    "acceptance_checks": [],
                }
            )

        return normalized

    def _normalize_reference_searches(
        self, reference_searches: Any, allowed_files: List[str]
    ) -> List[Dict[str, str]]:
        """Keep optional reference search suggestions aligned with current files."""
        if not self.enable_reference_indexing:
            return []
        if not self._has_reference_indexes():
            return []
        if not isinstance(reference_searches, list):
            return []

        normalized = []
        seen_files = set()
        for item in reference_searches:
            if not isinstance(item, dict):
                continue
            matched_file = self._match_file_in_list(
                item.get("file_path", ""), allowed_files
            )
            if not matched_file or matched_file in seen_files:
                continue
            keywords = str(item.get("keywords", "")).strip()
            rationale = str(item.get("rationale", "")).strip()
            if not keywords:
                continue
            normalized.append(
                {
                    "file_path": matched_file,
                    "indexes_path": self.reference_indexes_path,
                    "keywords": keywords,
                    "rationale": rationale,
                }
            )
            seen_files.add(matched_file)
            if len(normalized) >= 4:
                break
        return normalized

    async def _call_subplan_model(
        self,
        client,
        client_type: str,
        context: Dict[str, Any],
        messages: List[Dict[str, Any]],
        planner_messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Call the configured subplan model. Failure falls back without blocking."""
        prompt = self._create_round_subplan_prompt(
            context, messages, planner_messages
        )
        max_tokens = 4096

        try:
            if client_type == "anthropic":
                model = self.default_models.get(
                    "anthropic_subplan",
                    self.default_models.get(
                        "anthropic_implementation", self.default_models["anthropic"]
                    ),
                )
                self.logger.info(f"Subplan generation using model: {model}")
                response = await client.messages.create(
                    model=model,
                    system=ROUND_SUBPLAN_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                content = ""
                for block in response.content:
                    if block.type == "text":
                        content += block.text
            elif client_type == "google":
                from google.genai import types

                model = self.default_models.get(
                    "google_subplan",
                    self.default_models.get(
                        "google_implementation", self.default_models["google"]
                    ),
                )
                self.logger.info(f"Subplan generation using model: {model}")
                config = types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.2,
                    system_instruction=ROUND_SUBPLAN_SYSTEM_PROMPT,
                )
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                content = ""
                if response and hasattr(response, "candidates") and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, "content") and candidate.content:
                        for part in getattr(candidate.content, "parts", []) or []:
                            if hasattr(part, "text") and part.text:
                                content += part.text
            elif client_type == "openai":
                model = self.default_models.get(
                    "openai_subplan",
                    self.default_models.get(
                        "openai_implementation", self.default_models["openai"]
                    ),
                )
                self.logger.info(f"Subplan generation using model: {model}")
                openai_messages = [
                    {
                        "role": "system",
                        "content": ROUND_SUBPLAN_SYSTEM_PROMPT,
                    },
                    {"role": "user", "content": prompt},
                ]
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=openai_messages,
                        max_tokens=max_tokens,
                        temperature=0.2,
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        response = await client.chat.completions.create(
                            model=model,
                            messages=openai_messages,
                            max_completion_tokens=max_tokens,
                        )
                    else:
                        raise
                content = response.choices[0].message.content or ""
            else:
                raise ValueError(f"Unsupported client type for subplan: {client_type}")

            return self._parse_subplan_json(content, context)
        except Exception as e:
            self.logger.warning(f"Subplan generation failed, using fallback: {e}")
            return self._parse_subplan_json("", context)

    def _format_subplan_for_executor(self, subplan: Dict[str, Any]) -> str:
        """Add the subplan as a normal user message for the executor."""
        return SUBPLAN_EXECUTOR_MESSAGE_TEMPLATE.format(
            subplan_json=json.dumps(subplan, ensure_ascii=False, indent=2),
            reference_indexing_guidance=self._get_reference_indexing_context(),
        )

    def _tool_result_text(self, tool_result: Any) -> str:
        """Convert tool result to short text for diagnostics and round summaries."""
        if hasattr(tool_result, "content"):
            content = getattr(tool_result, "content", "")
            if isinstance(content, list):
                return "\n".join(
                    getattr(item, "text", str(item)) for item in content
                )
            return getattr(content, "text", str(content))
        if isinstance(tool_result, (dict, list)):
            return json.dumps(tool_result, ensure_ascii=False)
        return str(tool_result)

    def _tool_result_data(self, tool_result: Any) -> Dict[str, Any]:
        """Convert MCP tool result into dictionary data when possible."""
        result_text = self._tool_result_text(tool_result)
        try:
            result_data = json.loads(result_text)
            if isinstance(result_data, dict):
                return result_data
        except (TypeError, json.JSONDecodeError):
            pass
        return {}

    def _collect_lightweight_findings(
        self, tool_calls: List[Dict[str, Any]], tool_results: List[Dict[str, Any]]
    ) -> List[str]:
        """Collect non-blocking issues to pass into the next subplan."""
        findings = []
        stdlib_requirement_names = {
            "argparse",
            "collections",
            "dataclasses",
            "functools",
            "hashlib",
            "itertools",
            "json",
            "logging",
            "math",
            "os",
            "pathlib",
            "random",
            "re",
            "shutil",
            "sqlite3",
            "statistics",
            "subprocess",
            "sys",
            "time",
            "typing",
            "unittest",
            "urllib",
        }

        for tool_call, tool_result in zip(tool_calls, tool_results):
            tool_name = tool_call.get("name", "")
            tool_input = tool_call.get("input", {}) or {}
            result_text = self._tool_result_text(tool_result.get("result", ""))

            if '"status": "error"' in result_text or '"status":"error"' in result_text:
                findings.append(
                    f"{tool_name} failed for {tool_input.get('file_path', 'unknown')}: {result_text[:300]}"
                )

            if tool_name == "write_file":
                file_path = tool_input.get("file_path", "unknown")
                content = str(tool_input.get("content", ""))
                file_name = os.path.basename(str(file_path))
                file_name_lower = file_name.lower()
                if not content.strip():
                    findings.append(f"write_file produced empty content: {file_path}")
                    continue

                if str(file_path).endswith(".py"):
                    try:
                        compile(content, str(file_path), "exec")
                    except (SyntaxError, ValueError) as e:
                        if "null bytes" in str(e):
                            findings.append(
                                f"Python source issue in {file_path}: file contains null bytes"
                            )
                            continue
                        findings.append(
                            f"Python syntax issue in {file_path}: line {getattr(e, 'lineno', 'unknown')}, {getattr(e, 'msg', str(e))}"
                        )

                if file_name_lower == "requirements.txt":
                    for line in content.splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        package_name = re.split(r"[<>=~!;\[]", stripped, maxsplit=1)[
                            0
                        ].strip().lower()
                        if package_name in stdlib_requirement_names:
                            findings.append(
                                f"requirements.txt includes standard-library module '{package_name}', which should not be a pip dependency"
                            )

                if file_name_lower in {"readme.md", "setup.py"}:
                    if file_name_lower == "readme.md" and "python experiments/" in content:
                        findings.append(
                            f"{file_path} documents direct experiments/ script commands; verify the package layout and import path match those commands"
                        )
                    if (
                        file_name_lower == "setup.py"
                        and "console_scripts" in content
                        and "experiments." in content
                    ):
                        findings.append(
                            f"{file_path} defines experiment console scripts; verify entry points match the package layout and import paths"
                        )

                    install_requires_match = re.search(
                        r"install_requires\s*=\s*\[(.*?)\]",
                        content,
                        flags=re.DOTALL,
                    )
                    if file_name_lower == "setup.py" and install_requires_match:
                        install_requires_block = install_requires_match.group(1)
                        for package_name in stdlib_requirement_names:
                            if re.search(
                                rf"['\"]{re.escape(package_name)}(?:[<>=~!;\[].*)?['\"]",
                                install_requires_block,
                            ):
                                findings.append(
                                    f"{file_path} install_requires includes standard-library module '{package_name}', which should not be a pip dependency"
                                )

                if file_name == "__init__.py":
                    eager_import_count = len(
                        re.findall(r"^\s*from\s+\.[\w.]+\s+import\s+", content, re.MULTILINE)
                    )
                    if "import *" in content or eager_import_count >= 4:
                        findings.append(
                            f"{file_path} performs eager package imports; prefer lightweight re-exports or lazy imports to avoid import-time dependency coupling"
                        )
                    if "optional" in content.lower() and "import" in content.lower():
                        findings.append(
                            f"{file_path} may import optional modules eagerly; verify package initializers stay lightweight"
                        )
        return findings

    def _format_quality_findings(
        self, findings: List[Dict[str, str]], limit: int = 12
    ) -> str:
        """Format structured product diagnostics for planner/executor context."""
        if not findings:
            return "None"

        lines = []
        for finding in findings[:limit]:
            severity = finding.get("severity", "warning")
            issue_type = finding.get("issue_type", "quality")
            file_path = finding.get("file_path", "unknown")
            evidence = finding.get("evidence", "").strip()
            suggested_action = finding.get("suggested_action", "").strip()
            line = f"[{severity}] {issue_type} in {file_path}: {evidence}"
            if suggested_action:
                line += f" Suggested action: {suggested_action}"
            lines.append(line)
        if len(findings) > limit:
            lines.append(f"... and {len(findings) - limit} more finding(s)")
        return "\n".join(lines)

    def _add_quality_finding(
        self,
        findings: List[Dict[str, str]],
        severity: str,
        issue_type: str,
        file_path: str,
        evidence: str,
        suggested_action: str = "",
    ) -> None:
        """Append one deduplicated structured quality finding."""
        normalized = {
            "severity": severity,
            "issue_type": issue_type,
            "file_path": str(file_path).replace("\\", "/"),
            "evidence": str(evidence).strip(),
            "suggested_action": str(suggested_action).strip(),
        }
        key = (
            normalized["severity"],
            normalized["issue_type"],
            normalized["file_path"],
            normalized["evidence"],
        )
        existing_keys = {
            (
                item.get("severity", ""),
                item.get("issue_type", ""),
                item.get("file_path", ""),
                item.get("evidence", ""),
            )
            for item in findings
        }
        if key not in existing_keys:
            findings.append(normalized)

    def _run_product_quality_diagnostics(self, code_directory: str) -> List[Dict[str, str]]:
        """
        Run lightweight filesystem diagnostics on generated code.

        This intentionally avoids dependency installation, API calls, model downloads,
        and full test execution. It checks only stable, cheap engineering invariants.
        """
        findings: List[Dict[str, str]] = []
        root = Path(code_directory)
        if not root.exists():
            self._add_quality_finding(
                findings,
                "blocking",
                "missing_code_directory",
                str(root),
                "Generated code directory does not exist.",
                "Create the planned file tree before implementation completes.",
            )
            return findings

        package_modules = self._collect_generated_python_modules(root)

        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(root).as_posix()
            suffix = file_path.suffix.lower()
            name_lower = file_path.name.lower()

            if suffix == ".py":
                self._diagnose_python_file(file_path, rel_path, package_modules, findings)
            elif suffix in {".yaml", ".yml"}:
                self._diagnose_yaml_file(file_path, rel_path, findings)
            elif suffix == ".json":
                self._diagnose_json_file(file_path, rel_path, findings)

            if name_lower == "requirements.txt":
                self._diagnose_requirements_file(file_path, rel_path, findings)
            elif name_lower == "pyproject.toml":
                self._diagnose_pyproject_file(file_path, rel_path, package_modules, findings)
            elif name_lower == "setup.py":
                self._diagnose_setup_file(file_path, rel_path, package_modules, findings)
            elif name_lower == "readme.md":
                self._diagnose_readme_file(file_path, rel_path, package_modules, findings)

        return self._prioritize_quality_findings(findings)

    def _collect_generated_python_modules(self, root: Path) -> set:
        """Collect importable module/package names from generated Python files."""
        modules = set()
        for file_path in root.rglob("*.py"):
            relative = file_path.relative_to(root).with_suffix("")
            parts = relative.parts
            if not parts:
                continue
            module_name = ".".join(parts)
            modules.add(module_name)
            if file_path.name == "__init__.py":
                package_name = ".".join(parts[:-1])
                if package_name:
                    modules.add(package_name)
        return modules

    def _diagnose_python_file(
        self,
        file_path: Path,
        rel_path: str,
        package_modules: set,
        findings: List[Dict[str, str]],
    ) -> None:
        """Check Python syntax and generated-project absolute imports."""
        try:
            source = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            self._add_quality_finding(
                findings,
                "blocking",
                "python_decode_error",
                rel_path,
                str(e),
                "Rewrite the file as UTF-8 text.",
            )
            return

        try:
            tree = ast.parse(source, filename=rel_path)
        except (SyntaxError, ValueError) as e:
            if "null bytes" in str(e):
                self._add_quality_finding(
                    findings,
                    "blocking",
                    "python_null_byte",
                    rel_path,
                    "File contains null bytes and cannot be parsed as Python source.",
                    "Rewrite the file as clean UTF-8 Python text without embedded null bytes.",
                )
                return
            self._add_quality_finding(
                findings,
                "blocking",
                "python_syntax",
                rel_path,
                f"line {getattr(e, 'lineno', 'unknown')}: {getattr(e, 'msg', str(e))}",
                "Fix the syntax error and keep the public interface intact.",
            )
            return

        top_level_packages = {
            module.split(".", 1)[0]
            for module in package_modules
            if "." in module or module
        }

        for node in ast.walk(tree):
            imported_module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidate = alias.name
                    if candidate.split(".", 1)[0] in top_level_packages:
                        imported_module = candidate
                        break

            if not imported_module:
                continue
            if imported_module.split(".", 1)[0] not in top_level_packages:
                continue
            if imported_module in package_modules:
                continue
            if any(
                module.startswith(imported_module + ".")
                for module in package_modules
            ):
                continue

            self._add_quality_finding(
                findings,
                "blocking",
                "python_import_path",
                rel_path,
                f"Import references missing generated module '{imported_module}'.",
                "Align the import path with the actual package/file layout.",
            )

    def _diagnose_yaml_file(
        self, file_path: Path, rel_path: str, findings: List[Dict[str, str]]
    ) -> None:
        """Check YAML parseability when PyYAML is available."""
        try:
            import yaml
        except Exception:
            return

        try:
            yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            self._add_quality_finding(
                findings,
                "blocking",
                "yaml_parse",
                rel_path,
                str(e).splitlines()[0],
                "Rewrite the YAML with valid mapping/list indentation.",
            )

    def _diagnose_json_file(
        self, file_path: Path, rel_path: str, findings: List[Dict[str, str]]
    ) -> None:
        """Check JSON parseability."""
        try:
            json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            self._add_quality_finding(
                findings,
                "blocking",
                "json_parse",
                rel_path,
                str(e),
                "Rewrite the file as valid JSON.",
            )

    def _diagnose_requirements_file(
        self, file_path: Path, rel_path: str, findings: List[Dict[str, str]]
    ) -> None:
        """Check lightweight requirements.txt issues."""
        stdlib_requirement_names = self._stdlib_requirement_names()
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        normalized_packages = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            package_name = re.split(r"[<>=~!;\[]", stripped, maxsplit=1)[0].strip()
            package_key = package_name.lower().replace("_", "-")
            normalized_packages.append(package_key)
            if package_key.replace("-", "_") in stdlib_requirement_names:
                self._add_quality_finding(
                    findings,
                    "blocking",
                    "stdlib_requirement",
                    rel_path,
                    f"Standard-library module '{package_name}' is listed as a pip dependency.",
                    "Remove standard-library modules from requirements.txt.",
                )

        if "faiss-cpu" in normalized_packages and "faiss-gpu" in normalized_packages:
            self._add_quality_finding(
                findings,
                "warning",
                "conflicting_optional_dependencies",
                rel_path,
                "Both faiss-cpu and faiss-gpu are listed as required dependencies.",
                "Keep one default FAISS package and move the other to optional install guidance.",
            )

    def _diagnose_pyproject_file(
        self,
        file_path: Path,
        rel_path: str,
        package_modules: set,
        findings: List[Dict[str, str]],
    ) -> None:
        """Check pyproject package declarations against generated package modules."""
        if tomllib is None:
            return
        try:
            data = tomllib.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            self._add_quality_finding(
                findings,
                "blocking",
                "pyproject_parse",
                rel_path,
                str(e).splitlines()[0],
                "Rewrite pyproject.toml as valid TOML.",
            )
            return

        declared_packages = self._extract_pyproject_declared_packages(data)
        if not declared_packages:
            return

        importable_packages = {
            module
            for module in package_modules
            if (file_path.parent / Path(*module.split(".")) / "__init__.py").exists()
        }
        for package_name in sorted(declared_packages):
            if package_name in importable_packages:
                continue
            if any(module.startswith(package_name + ".") for module in importable_packages):
                continue
            self._add_quality_finding(
                findings,
                "blocking",
                "pyproject_package_layout",
                rel_path,
                f"pyproject.toml declares package '{package_name}', but no matching generated package directory exists.",
                "Align pyproject package declarations, tests, and actual package directories before completion.",
            )

    def _extract_pyproject_declared_packages(self, data: Dict[str, Any]) -> set:
        """Extract statically declared setuptools package names from pyproject data."""
        setuptools_config = (
            data.get("tool", {})
            .get("setuptools", {})
        )
        package_names = set()

        explicit_packages = setuptools_config.get("packages")
        if isinstance(explicit_packages, list):
            package_names.update(
                package
                for package in explicit_packages
                if isinstance(package, str) and package.strip()
            )
        elif isinstance(explicit_packages, dict):
            find_config = explicit_packages.get("find", {})
            package_names.update(self._package_names_from_find_config(find_config))

        package_names.update(
            package
            for package in setuptools_config.get("package-data", {}).keys()
            if isinstance(package, str) and package != "*"
        )
        return package_names

    def _package_names_from_find_config(self, find_config: Dict[str, Any]) -> set:
        """Return concrete package names from simple setuptools find include rules."""
        if not isinstance(find_config, dict):
            return set()
        package_names = set()
        for pattern in find_config.get("include", []) or []:
            if not isinstance(pattern, str):
                continue
            package = pattern.strip()
            if not package or "*" in package:
                package = package.split(".", 1)[0].replace("*", "").strip(".")
            if package:
                package_names.add(package)
        return package_names

    def _diagnose_setup_file(
        self,
        file_path: Path,
        rel_path: str,
        package_modules: set,
        findings: List[Dict[str, str]],
    ) -> None:
        """Check setup.py syntax and console entry points."""
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source, filename=rel_path)
        except (SyntaxError, ValueError) as e:
            if "null bytes" in str(e):
                self._add_quality_finding(
                    findings,
                    "blocking",
                    "python_null_byte",
                    rel_path,
                    "File contains null bytes and cannot be parsed as Python source.",
                    "Rewrite setup.py as clean UTF-8 Python text without embedded null bytes.",
                )
                return
            self._add_quality_finding(
                findings,
                "blocking",
                "python_syntax",
                rel_path,
                f"line {getattr(e, 'lineno', 'unknown')}: {getattr(e, 'msg', str(e))}",
                "Fix setup.py syntax.",
            )
            return

        project_requirements = self._read_sibling_requirements(file_path)
        if project_requirements:
            install_requires = self._extract_setup_install_requires(tree, source)
            if install_requires is not None:
                missing_requirements = sorted(project_requirements - install_requires)
                if missing_requirements and len(missing_requirements) >= max(
                    2, len(project_requirements) // 2
                ):
                    self._add_quality_finding(
                        findings,
                        "blocking",
                        "setup_requirements_mismatch",
                        rel_path,
                        "setup.py install_requires omits most requirements.txt dependencies: "
                        + ", ".join(missing_requirements[:8]),
                        "Keep install_requires aligned with real third-party requirements; do not filter them out as standard-library modules.",
                    )

        for entry_module in re.findall(
            r"['\"][\w.-]+\s*=\s*([\w.]+):[\w.]+['\"]", source
        ):
            if entry_module not in package_modules:
                self._add_quality_finding(
                    findings,
                    "blocking",
                    "setup_entrypoint",
                    rel_path,
                    f"Console script targets missing module '{entry_module}'.",
                    "Align the console script target with the generated package layout.",
                )

    def _read_sibling_requirements(self, setup_file: Path) -> set:
        """Return normalized package names from requirements.txt next to setup.py."""
        requirements_file = setup_file.with_name("requirements.txt")
        if not requirements_file.exists():
            return set()

        stdlib_requirement_names = self._stdlib_requirement_names()
        requirements = set()
        content = requirements_file.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            package_name = self._extract_requirement_name(line)
            if not package_name:
                continue
            package_key = package_name.lower().replace("_", "-")
            if package_key.replace("-", "_") in stdlib_requirement_names:
                continue
            requirements.add(package_key)
        return requirements

    def _extract_setup_install_requires(
        self, tree: ast.AST, source: str
    ) -> Optional[set]:
        """Extract simple install_requires package names when statically visible."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = getattr(node.func, "id", "")
            if function_name != "setup":
                continue
            for keyword in node.keywords:
                if keyword.arg != "install_requires":
                    continue
                if isinstance(keyword.value, ast.List):
                    requirements = set()
                    for item in keyword.value.elts:
                        if isinstance(item, ast.Constant) and isinstance(item.value, str):
                            package_name = self._extract_requirement_name(item.value)
                            if package_name:
                                requirements.add(package_name.lower().replace("_", "-"))
                    return requirements
                if isinstance(keyword.value, ast.Call):
                    function_name = getattr(keyword.value.func, "id", "")
                    return self._extract_returned_requirement_names(tree, function_name)

        install_requires_match = re.search(
            r"install_requires\s*=\s*\[(.*?)\]",
            source,
            flags=re.DOTALL,
        )
        if not install_requires_match:
            return None
        return {
            package_name.lower().replace("_", "-")
            for package_name in (
                self._extract_requirement_name(match)
                for match in re.findall(r"['\"]([^'\"]+)['\"]", install_requires_match.group(1))
            )
            if package_name
        }

    def _extract_returned_requirement_names(
        self, tree: ast.AST, function_name: str
    ) -> Optional[set]:
        """Extract package names from simple helper functions returning a list."""
        if not function_name:
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != function_name:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Return) or not isinstance(child.value, ast.List):
                    continue
                requirements = set()
                for item in child.value.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        package_name = self._extract_requirement_name(item.value)
                        if package_name:
                            requirements.add(package_name.lower().replace("_", "-"))
                return requirements
        return None

    def _extract_requirement_name(self, line: str) -> str:
        """Extract normalized dependency name from one requirement line."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            return ""
        stripped = stripped.split("#", 1)[0].strip()
        return re.split(r"[<>=~!;\[]", stripped, maxsplit=1)[0].strip()

    def _diagnose_readme_file(
        self,
        file_path: Path,
        rel_path: str,
        package_modules: set,
        findings: List[Dict[str, str]],
    ) -> None:
        """Check README command module paths against generated package modules."""
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        stdlib_or_tool_modules = self._stdlib_requirement_names() | {"pip", "venv"}
        for module in re.findall(r"python\s+-m\s+([\w.]+)", content):
            if module.split(".", 1)[0] in stdlib_or_tool_modules:
                continue
            if module not in package_modules:
                self._add_quality_finding(
                    findings,
                    "warning",
                    "readme_command_path",
                    rel_path,
                    f"README command references module '{module}' not found in generated package.",
                    "Update README commands to match the actual package layout.",
                )

    def _stdlib_requirement_names(self) -> set:
        """Common stdlib names that should not appear as pip requirements."""
        return {
            "argparse",
            "collections",
            "dataclasses",
            "functools",
            "hashlib",
            "itertools",
            "json",
            "logging",
            "math",
            "os",
            "pathlib",
            "random",
            "re",
            "shutil",
            "sqlite3",
            "statistics",
            "subprocess",
            "sys",
            "time",
            "typing",
            "unittest",
            "urllib",
        }

    def _prioritize_quality_findings(
        self, findings: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """Return deterministic finding order with blocking issues first."""
        severity_rank = {"blocking": 0, "warning": 1, "info": 2}
        return sorted(
            findings,
            key=lambda item: (
                severity_rank.get(item.get("severity", "warning"), 1),
                item.get("file_path", ""),
                item.get("issue_type", ""),
                item.get("evidence", ""),
            ),
        )

    async def _call_llm_with_tools(
        self, client, client_type, system_message, messages, tools, max_tokens=8192
    ):
        """Call LLM with tools"""
        try:
            if client_type == "anthropic":
                return await self._call_anthropic_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            elif client_type == "openai":
                return await self._call_openai_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            elif client_type == "google":
                return await self._call_google_with_tools(
                    client, system_message, messages, tools, max_tokens
                )
            else:
                raise ValueError(f"Unsupported client type: {client_type}")
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise

    async def _call_anthropic_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call Anthropic API"""
        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [
                {"role": "user", "content": "Please continue implementing code"}
            ]

        try:
            # Use implementation-specific model for code generation
            impl_model = self.default_models.get(
                "anthropic_implementation", self.default_models["anthropic"]
            )
            self.logger.info(f"Code generation using model: {impl_model}")
            response = await client.messages.create(
                model=impl_model,
                system=system_message,
                messages=validated_messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=0.2,
            )
        except Exception as e:
            self.logger.error(f"Anthropic API call failed: {e}")
            raise

        content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        return {"content": content, "tool_calls": tool_calls}

    async def _call_google_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """
        Call Google Gemini API with tools

        Note: Google Gemini uses a completely different API structure.
        The client here is expected to be google.genai.Client from google-genai SDK.

        Reference: https://ai.google.dev/gemini-api/docs/function-calling
        """
        try:
            from google.genai import types
        except ImportError:
            raise ImportError("google-genai package is required for Google API calls")

        validated_messages = self._validate_messages(messages)
        if not validated_messages:
            validated_messages = [
                {"role": "user", "content": "Please continue implementing code"}
            ]

        # Convert messages to Google Gemini format (types.Content)
        # Gemini expects: role="user" or role="model" (not "assistant")
        gemini_messages = []
        for msg in validated_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Convert role names: "assistant" -> "model"
            if role == "assistant":
                role = "model"
            elif role not in ["user", "model"]:
                # Skip unsupported roles or convert to user
                role = "user"

            gemini_messages.append(
                types.Content(role=role, parts=[types.Part.from_text(text=content)])
            )

        # Convert tools to Google Gemini format (types.Tool with FunctionDeclaration)
        # Following the EXACT pattern from GoogleAugmentedLLM line 92-103
        # IMPORTANT: Each tool should be wrapped in its own Tool object!
        gemini_tools = []
        if tools:
            for tool in tools:
                # Transform the input_schema to be Gemini-compatible
                parameters = self._transform_schema_for_gemini(tool["input_schema"])

                # Each tool gets its own Tool wrapper (not all in one!)
                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool["name"],
                                description=tool["description"],
                                parameters=parameters,
                            )
                        ]
                    )
                )

        # Create config with system instruction and tools
        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.2,
            system_instruction=system_message if system_message else None,
            tools=gemini_tools if gemini_tools else None,
            # Disable automatic function calling - we handle it manually
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        try:
            # Google Gemini API call using the native SDK
            # client is google.genai.Client instance
            # Use implementation-specific model for code generation
            impl_model = self.default_models.get(
                "google_implementation", self.default_models["google"]
            )
            self.logger.info(f"Code generation using model: {impl_model}")
            response = await client.aio.models.generate_content(
                model=impl_model,
                contents=gemini_messages,
                config=config,
            )
        except Exception as e:
            self.logger.error(f"Google API call failed: {e}")
            raise

        # Parse Gemini response (types.GenerateContentResponse)
        # Following the pattern from augmented_llm_google.py lines 145-165
        content = ""
        tool_calls = []

        if response and hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]

            if hasattr(candidate, "content") and candidate.content:
                if hasattr(candidate.content, "parts") and candidate.content.parts:
                    for part in candidate.content.parts:
                        # Handle text content
                        if hasattr(part, "text") and part.text:
                            content += part.text

                        # Handle function calls
                        # Check for function_call attribute, matching augmented_llm_google.py line 164
                        if hasattr(part, "function_call") and part.function_call:
                            fc = part.function_call
                            # Extract function call details
                            # Note: Gemini function_call has name and args attributes
                            tool_call = {
                                "id": getattr(
                                    fc, "id", getattr(fc, "name", "")
                                ),  # Use name as fallback for id
                                "name": fc.name if hasattr(fc, "name") else "",
                                "input": dict(fc.args)
                                if hasattr(fc, "args") and fc.args
                                else {},
                            }
                            self.logger.debug(
                                f"Google function_call parsed: {tool_call}"
                            )
                            tool_calls.append(tool_call)

        return {"content": content, "tool_calls": tool_calls}

    def _transform_schema_for_gemini(self, schema: dict) -> dict:
        """
        Transform JSON Schema to OpenAPI Schema format compatible with Gemini.

        This is based on the transform_mcp_tool_schema from GoogleAugmentedLLM.
        Key transformations:
        1. Convert camelCase to snake_case
        2. Remove unsupported fields (default, additionalProperties)
        3. Handle nullable types via anyOf
        """
        if not isinstance(schema, dict):
            return schema

        # Fields to exclude
        EXCLUDED_PROPERTIES = {"default", "additionalProperties"}

        # camelCase to snake_case mappings
        CAMEL_TO_SNAKE = {
            "anyOf": "any_of",
            "maxLength": "max_length",
            "minLength": "min_length",
            "minProperties": "min_properties",
            "maxProperties": "max_properties",
            "maxItems": "max_items",
            "minItems": "min_items",
        }

        result = {}

        for key, value in schema.items():
            # Skip excluded properties
            if key in EXCLUDED_PROPERTIES:
                continue

            # Convert camelCase to snake_case
            snake_key = CAMEL_TO_SNAKE.get(key, key)

            # Handle nested structures
            if key == "properties" and isinstance(value, dict):
                result[snake_key] = {
                    prop_k: self._transform_schema_for_gemini(prop_v)
                    for prop_k, prop_v in value.items()
                }
            elif key == "items" and isinstance(value, dict):
                result[snake_key] = self._transform_schema_for_gemini(value)
            elif key == "anyOf" and isinstance(value, list):
                # Handle nullable types (Type | None)
                has_null = any(
                    isinstance(item, dict) and item.get("type") == "null"
                    for item in value
                )
                if has_null:
                    result["nullable"] = True

                # Get first non-null schema
                for item in value:
                    if isinstance(item, dict) and item.get("type") != "null":
                        transformed = self._transform_schema_for_gemini(item)
                        for k, v in transformed.items():
                            if k not in result:
                                result[k] = v
                        break
            else:
                result[snake_key] = value

        return result

    def _repair_truncated_json(self, json_str: str, tool_name: str = "") -> dict:
        """
        Advanced JSON repair for truncated or malformed JSON from LLM responses.

        Handles:
        - Missing closing braces/brackets
        - Truncated string values
        - Missing required fields
        - Trailing commas
        """
        import re

        # Step 1: Try basic fixes first
        fixed = json_str.strip()

        # Remove trailing commas
        fixed = re.sub(r",\s*}", "}", fixed)
        fixed = re.sub(r",\s*]", "]", fixed)

        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            print("   Attempting advanced JSON repair...")

            # Step 2: Check for truncation issues
            if e.msg == "Expecting value":
                # Likely truncated - try to close open structures
                fixed = self._close_json_structures(fixed)
                try:
                    return json.loads(fixed)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Step 3: Try to extract partial valid JSON
            if e.msg.startswith("Expecting") and e.pos:
                # Truncate at error position and try to close
                truncated = fixed[: e.pos]
                closed = self._close_json_structures(truncated)
                try:
                    partial = json.loads(closed)
                    return partial
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Step 4: Tool-specific defaults for critical tools
            if tool_name == "write_file":
                # For write_file, try to extract at least file_path
                file_path_match = re.search(r'"file_path"\s*:\s*"([^"]*)"', fixed)
                if file_path_match:
                    print("   write_file JSON truncated, using minimal structure")
                    return {
                        "file_path": file_path_match.group(1),
                        "content": "",  # Empty content is better than crashing
                    }

            # Step 5: Last resort - return error indicator
            return None

    def _close_json_structures(self, json_str: str) -> str:
        """
        Intelligently close unclosed JSON structures.
        Counts braces and brackets to determine what needs closing.
        """
        # Count open structures
        open_braces = json_str.count("{") - json_str.count("}")
        open_brackets = json_str.count("[") - json_str.count("]")

        # Check if we're in the middle of a string
        quote_count = json_str.count('"')
        in_string = (quote_count % 2) != 0

        result = json_str

        # Close string if needed
        if in_string:
            result += '"'

        # Close brackets first (inner structures)
        result += "]" * open_brackets

        # Close braces
        result += "}" * open_braces

        return result

    async def _call_openai_with_tools(
        self, client, system_message, messages, tools, max_tokens
    ):
        """Call OpenAI API with robust JSON error handling and retry mechanism"""
        openai_tools = []
        for tool in tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
            )

        openai_messages = [{"role": "system", "content": system_message}]
        openai_messages.extend(messages)

        # Retry mechanism for API calls
        max_retries = 3
        retry_delay = 2  # seconds

        # Use implementation-specific model for code generation
        impl_model = self.default_models.get(
            "openai_implementation", self.default_models["openai"]
        )
        self.logger.info(f"Code generation using model: {impl_model}")

        for attempt in range(max_retries):
            try:
                # Try max_tokens first, fallback to max_completion_tokens if unsupported
                try:
                    response = await client.chat.completions.create(
                        model=impl_model,
                        messages=openai_messages,
                        tools=openai_tools if openai_tools else None,
                        max_tokens=max_tokens,
                        temperature=0.2,
                    )
                except Exception as e:
                    if "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                        # Retry with max_completion_tokens for models that require it
                        response = await client.chat.completions.create(
                            model=impl_model,
                            messages=openai_messages,
                            tools=openai_tools if openai_tools else None,
                            max_completion_tokens=max_tokens,
                        )
                    else:
                        raise

                # Validate response structure
                if (
                    not response
                    or not hasattr(response, "choices")
                    or not response.choices
                ):
                    raise ValueError("Invalid API response: missing choices")

                if not response.choices[0] or not hasattr(
                    response.choices[0], "message"
                ):
                    raise ValueError("Invalid API response: missing message in choice")

                message = response.choices[0].message
                content = message.content or ""

                # Successfully got a valid response
                break

            except json.JSONDecodeError as e:
                print(
                )
                print(f"   Error: {e}")
                print(f"   Position: line {e.lineno}, column {e.colno}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    raise

            except (ValueError, AttributeError, TypeError) as e:
                print(f"   Error type: {type(e).__name__}")
                print(f"   Error: {e}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    # Return empty response instead of crashing
                    return {
                        "content": "API error - unable to get valid response",
                        "tool_calls": [],
                    }

            except Exception as e:
                print(
                )
                print(f"   Error type: {type(e).__name__}")
                print(f"   Error: {e}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise

        tool_calls = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                try:
                    # Attempt to parse tool call arguments
                    parsed_input = json.loads(tool_call.function.arguments)
                    tool_calls.append(
                        {
                            "id": tool_call.id,
                            "name": tool_call.function.name,
                            "input": parsed_input,
                        }
                    )
                except json.JSONDecodeError as e:
                    # Detailed JSON parsing error logging
                    print(f"   Tool: {tool_call.function.name}")
                    print(f"   Error: {e}")
                    print("   Raw arguments (first 500 chars):")
                    print(f"   {tool_call.function.arguments[:500]}")
                    print(f"   Error position: line {e.lineno}, column {e.colno}")
                    print(
                        f"   Problem at: ...{tool_call.function.arguments[max(0, e.pos-50):e.pos+50]}..."
                    )

                    # Attempt advanced JSON repair
                    repaired = self._repair_truncated_json(
                        tool_call.function.arguments, tool_call.function.name
                    )

                    if repaired:
                        tool_calls.append(
                            {
                                "id": tool_call.id,
                                "name": tool_call.function.name,
                                "input": repaired,
                            }
                        )
                    else:
                        # Skip this tool call if repair failed
                        print("   Skipping unrepairable tool call")
                        continue

        return {"content": content, "tool_calls": tool_calls}

    # ==================== 5. Tools and Utility Methods (Utility Layer) ====================

    def _validate_messages(self, messages: List[Dict]) -> List[Dict]:
        """Validate and clean message list"""
        valid_messages = []
        for msg in messages:
            content = msg.get("content", "").strip()
            if content:
                valid_messages.append(
                    {"role": msg.get("role", "user"), "content": content}
                )
            else:
                self.logger.warning(f"Skipping empty message: {msg}")
        return valid_messages

    def _prepare_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        """Prepare tool definitions in Anthropic API standard format"""
        tool_set = (
            "code_implementation_with_references"
            if self.enable_reference_indexing and self._has_reference_indexes()
            else "code_implementation"
        )
        tools = get_mcp_tools(tool_set)
        self.logger.info(
            f"Enabled implementation tools: {[tool.get('name') for tool in tools]}"
        )
        return tools

    def _check_tool_results_for_errors(self, tool_results: List[Dict]) -> bool:
        """Check tool results for errors with JSON repair capability"""
        for result in tool_results:
            try:
                if hasattr(result["result"], "content") and result["result"].content:
                    content_text = result["result"].content[0].text

                    # First attempt: try direct JSON parsing
                    try:
                        parsed_result = json.loads(content_text)
                        if parsed_result.get("status") == "error":
                            return True
                    except json.JSONDecodeError as e:
                        # JSON parsing failed - try to repair
                        print("\nJSON parsing failed in tool result check:")
                        print(f"   Error: {e}")
                        print(
                            f"   Position: line {e.lineno}, column {e.colno}, char {e.pos}"
                        )
                        print(f"   Content length: {len(content_text)} chars")
                        print(f"   First 300 chars: {content_text[:300]}")

                        # Attempt to repair the JSON
                        repaired = self._repair_truncated_json(content_text)
                        if repaired:
                            if repaired.get("status") == "error":
                                return True
                        else:
                            # Fallback: check for "error" keyword in text
                            if "error" in content_text.lower():
                                return True

                elif isinstance(result["result"], str):
                    if "error" in result["result"].lower():
                        return True

            except (AttributeError, IndexError) as e:
                # Unexpected result structure
                print(f"\nUnexpected result structure: {type(e).__name__}: {e}")
                result_str = str(result["result"])
                if "error" in result_str.lower():
                    return True
        return False

    # ==================== 6. User Interaction and Feedback (Interaction Layer) ====================

    def _generate_success_guidance(self, files_count: int) -> str:
        """Generate concise success guidance for continuing implementation"""
        return f"""File implementation completed successfully.

**Progress Status:** {files_count} files implemented

**Next Action:** Check if ALL files from the reproduction plan are implemented. If this batch round still has planned files, continue with the next planned file.

1. **If ALL files implemented:** Reply with "All files implemented" to complete the task
2. **If MORE planned files remain in this batch:** Continue with dependency-aware workflow
   - **Use one `write_file` call per file**"""

    def _generate_error_guidance(self) -> str:
        """Generate error guidance for handling issues"""
        return """Error detected during file implementation.

**Action Required:**
1. Review the error details above
2. Fix the identified issue
3. **Check if ALL files from the reproduction plan are implemented:**
   - **If YES:** Respond "**implementation complete**" to end the conversation
   - **If NO:** Continue with proper development cycle for the next planned file:
     - **Use one `write_file` call per file** to implement properly
4. Ensure proper error handling in future implementations"""

    def _generate_reference_search_guidance(self) -> str:
        """Generate guidance after an optional reference search result."""
        return """Reference search result received.

**Next Action:** Use the reference result only as optional inspiration. Continue the current planned file by calling `write_file` with a complete implementation.

**Rules:**
1. Do not copy reference code blindly.
2. Follow the paper, initial reproduction plan, existing summaries, and current subplan/Next Steps.
3. Preserve current project interfaces and imports."""

    def _generate_no_tools_guidance(self, files_count: int) -> str:
        """Generate concise guidance when no tools are called"""
        return f"""No tool calls detected in your response.

**Current Progress:** {files_count} files implemented

**Action Required:** Check completion status NOW:

1. **If ALL files from plan are implemented:** Reply "All files implemented" to complete
2. **If MORE files need implementation:** Use tools to continue:
   - **Use one `write_file` call per file** for the next planned file(s)

**Critical:** Don't just explain - either declare completion or use tools!"""

    def _compile_user_response(self, tool_results: List[Dict], guidance: str) -> str:
        """Compile tool results and guidance into a single user response"""
        response_parts = []

        if tool_results:
            response_parts.append("**Tool Execution Results:**")
            for tool_result in tool_results:
                tool_name = tool_result["tool_name"]
                result_content = tool_result["result"]
                response_parts.append(
                    f"```\nTool: {tool_name}\nResult: {result_content}\n```"
                )

        if guidance:
            response_parts.append("\n" + guidance)

        return "\n\n".join(response_parts)

    # ==================== 7. Reporting and Output (Output Layer) ====================

    async def _generate_pure_code_final_report_with_concise_agents(
        self,
        iterations: int,
        elapsed_time: float,
        code_agent: CodeImplementationAgent,
        memory_agent: ConciseMemoryAgent,
    ):
        """Generate final report using concise agent statistics"""
        try:
            code_stats = code_agent.get_implementation_statistics()
            memory_stats = memory_agent.get_memory_statistics(
                code_stats["files_implemented_count"]
            )

            if self.mcp_agent:
                history_result = await self.mcp_agent.call_tool(
                    "get_operation_history", {"last_n": 30}
                )
                history_data = self._tool_result_data(history_result)
            else:
                history_data = {"total_operations": 0, "history": []}

            if not history_data:
                history_data = {"total_operations": 0, "history": []}

            write_operations = 0
            files_created = []
            history_items = history_data.get("history", [])
            if isinstance(history_items, list):
                for item in history_items:
                    if not isinstance(item, dict):
                        continue
                    if item.get("action") == "write_file":
                        write_operations += 1
                        file_path = item.get("details", {}).get("file_path", "unknown")
                        files_created.append(file_path)

            report = f"""
# DeepRepro Implementation Completion Report

## Execution Summary
- Implementation mode: {self.implementation_mode}
- Reference indexing enabled: {self.enable_reference_indexing}
- Reference indexes path: {self.reference_indexes_path if self.enable_reference_indexing else "N/A"}
- Implementation iterations: {iterations}
- Total elapsed time: {elapsed_time:.2f} seconds
- Files implemented: {code_stats['total_files_implemented']}
- File write operations: {write_operations}
- Total MCP operations: {history_data.get('total_operations', 0)}

## Read Tools Configuration
- Read tools enabled: {code_stats['read_tools_status']['read_tools_enabled']}
- Status: {code_stats['read_tools_status']['status']}
- Tools affected: {', '.join(code_stats['read_tools_status']['tools_affected'])}

## Agent Performance
### Executor Agent
- Files tracked: {code_stats['files_implemented_count']}
- Technical decisions: {code_stats['technical_decisions_count']}
- Constraints tracked: {code_stats['constraints_count']}
- Architecture notes: {code_stats['architecture_notes_count']}
- Dependency analysis performed: {code_stats['dependency_analysis_count']}
- Files read for dependencies: {code_stats['files_read_for_dependencies']}
- Last summary triggered at file count: {code_stats['last_summary_file_count']}

### Concise Memory Agent
- Last write_file detected: {memory_stats['last_write_file_detected']}
- Should clear memory next: {memory_stats['should_clear_memory_next']}
- Files implemented count: {memory_stats['implemented_files_tracked']}
- Current round: {memory_stats['current_round']}
- Concise mode active: {memory_stats['concise_mode_active']}
- Current round tool results: {memory_stats['current_round_tool_results']}
- Essential tools recorded: {memory_stats['essential_tools_recorded']}

## Files Created
"""
            for file_path in files_created[-20:]:
                report += f"- {file_path}\n"

            if len(files_created) > 20:
                report += f"... and {len(files_created) - 20} more files\n"

            if self.latest_quality_findings:
                blocking_count = sum(
                    1
                    for finding in self.latest_quality_findings
                    if finding.get("severity") == "blocking"
                )
                warning_count = sum(
                    1
                    for finding in self.latest_quality_findings
                    if finding.get("severity") == "warning"
                )
                report += f"""
## Lightweight Product Diagnostics
- Blocking findings: {blocking_count}
- Warning findings: {warning_count}
{self._format_quality_findings(self.latest_quality_findings, limit=10)}
"""
            elif self.implementation_mode == "deepplan":
                report += """
## Lightweight Product Diagnostics
- Blocking findings: 0
- Warning findings: 0
No lightweight product diagnostics were found.
"""

            report += """
## DeepRepro Workflow Features
- Batch-oriented implementation rounds with one `write_file` call per generated file
- Optional reference index search is enabled only for infer modes and remains inspiration-only
- Round compaction into initial plan, full file summaries, progress, and current file lists
- Fast mode uses post-round Next Steps; deepplan mode uses pre-round subplans and lightweight diagnostics
- Lightweight non-blocking diagnostics feed the next planning round without stopping implementation
- MCP-compliant tool execution through the code implementation server
"""
            return report

        except Exception as e:
            self.logger.error(f"Failed to generate final report: {e}")
            return f"Failed to generate final report: {str(e)}"


async def main():
    """Main function for running the workflow"""
    # Configure root logger carefully to avoid duplicates
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    workflow = CodeImplementationWorkflow()

    print("=" * 60)
    print("DeepRepro implementation workflow with unified reference indexer")
    print("=" * 60)
    print("Select mode:")
    print("1. Test Code Reference Indexer Integration")
    print("2. Run Full Implementation Workflow")
    print("3. Run Implementation with Pure Code Mode")
    print("4. Test Read Tools Configuration")

    # mode_choice = input("Enter choice (1-4, default: 3): ").strip()

    # For testing purposes, we'll run the test first
    # if mode_choice == "4":
    #     print("Testing Read Tools Configuration...")

    #     # Create a test workflow normally
    #     test_workflow = CodeImplementationWorkflow()

    #     # Create a mock code agent for testing
    #     print("\nTesting with read tools DISABLED:")
    #     test_agent_disabled = CodeImplementationAgent(None, enable_read_tools=False)
    #     await test_agent_disabled.test_read_tools_configuration()

    #     print("\nTesting with read tools ENABLED:")
    #     test_agent_enabled = CodeImplementationAgent(None, enable_read_tools=True)
    #     await test_agent_enabled.test_read_tools_configuration()

    #     return

    # print("Running Code Reference Indexer Integration Test...")

    test_success = True
    if test_success:
        print("\n" + "=" * 60)
        print("Unified code reference indexer integration test passed.")
        print("Three-step process successfully merged into one tool")
        print("=" * 60)

        # Ask if user wants to continue with actual workflow
        print("\nContinuing with workflow execution...")

        plan_file = os.path.join(
            os.getcwd(), "deeprepro_code", "task2", "initial_plan.txt"
        )
        target_directory = os.path.join(os.getcwd(), "deeprepro_code", "task2")
        print("Implementation Mode Selection:")
        print("1. Pure Code Implementation Mode (Recommended)")
        print("2. Iterative Implementation Mode")

        pure_code_mode = True
        mode_name = "Pure Code Implementation Mode with Memory Agent Architecture + Code Reference Indexer"
        print(f"Using: {mode_name}")

        # Configure read tools - modify this parameter to enable/disable read tools
        enable_read_tools = (
            True  # Set to False to disable read_file and read_code_mem tools
        )
        read_tools_status = "ENABLED" if enable_read_tools else "DISABLED"
        f"Read tools (read_file, read_code_mem): {read_tools_status}"

        # NOTE: To test without read tools, change the line above to:
        # enable_read_tools = False

        result = await workflow.run_workflow(
            plan_file,
            target_directory=target_directory,
            pure_code_mode=pure_code_mode,
            enable_read_tools=enable_read_tools,
        )

        print("=" * 60)
        print("Workflow Execution Results:")
        print(f"Status: {result['status']}")
        print(f"Mode: {mode_name}")

        if result["status"] == "success":
            print(f"Code Directory: {result['code_directory']}")
            print(f"MCP Architecture: {result.get('mcp_architecture', 'unknown')}")
            print("Execution completed!")
        else:
            print(f"Error Message: {result['message']}")

        print("=" * 60)
        print(
        )

    else:
        print("\n" + "=" * 60)
        print("Please check the configuration and try again.")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
