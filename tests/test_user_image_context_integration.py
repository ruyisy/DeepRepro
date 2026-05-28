import asyncio
import importlib.util
import json
import os
import shutil
import sys
import time
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


pdf_downloader_module = load_module(
    "paper_to_code_figure_pdf_downloader_test_module",
    "tools/pdf_downloader.py",
)
orchestration_module = load_module(
    "paper_to_code_figure_orchestration_test_module",
    "workflows/agent_orchestration_engine.py",
)


class FakeAnalysisAgent:
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


class UserImageContextIntegrationTests(unittest.TestCase):
    def test_figure_context_requires_user_uploaded_auxiliary_images(self):
        temp_path = ROOT / "tests" / "_tmp_auxiliary_image_gate"
        if temp_path.exists():
            shutil.rmtree(temp_path)
        temp_path.mkdir(parents=True, exist_ok=True)

        try:
            dir_info = {
                "paper_dir": str(temp_path),
                "user_image_context_path": str(temp_path / "user_image_context.json"),
                "auxiliary_image_paths": [],
            }
            result = asyncio.run(
                orchestration_module.orchestrate_user_image_context(
                    dir_info,
                    logger=mock.Mock(),
                )
            )
        finally:
            if temp_path.exists():
                shutil.rmtree(temp_path)

        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["reason"], "no_auxiliary_images")
        self.assertFalse(dir_info["user_image_ready"])

    def test_disabled_user_image_context_does_not_force_plan_refresh_from_stale_context(self):
        temp_path = ROOT / "tests" / "_tmp_disabled_figure_plan_refresh"
        if temp_path.exists():
            shutil.rmtree(temp_path)
        temp_path.mkdir(parents=True, exist_ok=True)

        try:
            initial_plan_path = temp_path / "initial_plan.txt"
            user_image_context_path = temp_path / "user_image_context.json"
            initial_plan_path.write_text("existing plan", encoding="utf-8")
            user_image_context_path.write_text("{}", encoding="utf-8")
            now = time.time()
            os.utime(initial_plan_path, (now, now))
            os.utime(user_image_context_path, (now + 10, now + 10))

            self.assertFalse(
                orchestration_module._should_refresh_initial_plan(
                    str(initial_plan_path),
                    "",
                )
            )
        finally:
            if temp_path.exists():
                shutil.rmtree(temp_path)

    def test_resolve_pdf_conversion_paths_preserves_original_pdf(self):
        pdf_source_path, markdown_output_path, preserved_pdf_path = (
            pdf_downloader_module._resolve_pdf_conversion_paths("deepcode_lab/papers/7/7.md")
        )

        self.assertEqual(pdf_source_path, "deepcode_lab/papers/7/7.pdf")
        self.assertEqual(markdown_output_path, "deepcode_lab/papers/7/7.md")
        self.assertEqual(preserved_pdf_path, "deepcode_lab/papers/7/7.pdf")

    def test_build_multimodal_algorithm_message_supports_multiple_pages(self):
        temp_path = ROOT / "tests" / "_tmp_multimodal_message"
        if temp_path.exists():
            shutil.rmtree(temp_path)
        temp_path.mkdir(parents=True, exist_ok=True)

        try:
            image_dir = temp_path / "figure_assets" / "page_snapshots"
            image_dir.mkdir(parents=True, exist_ok=True)
            for name in ["page_003.png", "page_005.png"]:
                (image_dir / name).write_bytes(b"fake-image-bytes")

            message = orchestration_module._build_multimodal_algorithm_message(
                base_message="base paper analysis",
                paper_dir=str(temp_path),
                user_image_context={
                    "selected_image_paths": [
                        "figure_assets/page_snapshots/page_003.png",
                        "figure_assets/page_snapshots/page_005.png",
                    ],
                    "selection_strategy": "user_uploaded_images",
                    "selected_pages": [3, 5],
                    "page_contexts": [
                        {
                            "page_number": 3,
                            "figure_label": "Fig. 2",
                            "caption": "Overall framework of the model",
                            "page_excerpt": "Figure 2 shows the full architecture.",
                        },
                        {
                            "page_number": 5,
                            "figure_label": "Fig. 4",
                            "caption": "Training workflow of the model",
                            "page_excerpt": "Figure 4 shows the training path.",
                        },
                    ],
                },
            )

            self.assertIsInstance(message, list)
            self.assertEqual(message[0]["role"], "user")
            content = message[0]["content"]
            self.assertEqual(content[0]["type"], "text")
            self.assertIn("USER IMAGE CONTEXT START", content[0]["text"])
            self.assertIn("selected_pages: [3, 5]", content[0]["text"])
            self.assertIn("page_1_figure_label: Fig. 2", content[0]["text"])
            self.assertIn("page_2_figure_label: Fig. 4", content[0]["text"])
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertEqual(content[2]["type"], "image_url")
        finally:
            if temp_path.exists():
                shutil.rmtree(temp_path)

    def test_run_code_analyzer_ignores_user_image_context_when_path_not_provided(self):
        temp_path = ROOT / "tests" / "_tmp_no_figure_context_load"
        if temp_path.exists():
            shutil.rmtree(temp_path)
        temp_path.mkdir(parents=True, exist_ok=True)

        try:
            (temp_path / "7.md").write_text("paper body", encoding="utf-8")
            (temp_path / "user_image_context.json").write_text(
                json.dumps(
                    {
                        "selected_page": 3,
                        "selected_image_path": "figure_assets/page_snapshots/page_003.png",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(orchestration_module, "Agent", FakeAnalysisAgent), mock.patch.object(
                orchestration_module,
                "get_preferred_llm_class",
                return_value=object(),
            ), mock.patch.object(
                orchestration_module,
                "_assess_output_completeness",
                return_value=1.0,
            ), mock.patch.object(
                orchestration_module,
                "_load_optional_json_file",
                wraps=orchestration_module._load_optional_json_file,
            ) as mocked_loader:
                result = asyncio.run(
                    orchestration_module.run_code_analyzer(
                        str(temp_path),
                        logger=mock.Mock(),
                        use_segmentation=False,
                        user_image_context_path="",
                    )
                )

            self.assertEqual(result, "planner-output")
            self.assertEqual(mocked_loader.call_count, 0)
        finally:
            if temp_path.exists():
                shutil.rmtree(temp_path)

    def test_find_primary_paper_markdown_file_prefers_numeric_paper_markdown(self):
        temp_path = ROOT / "tests" / "_tmp_figure_analysis_selection"
        if temp_path.exists():
            shutil.rmtree(temp_path)
        temp_path.mkdir(parents=True, exist_ok=True)
        try:
            (temp_path / "notes.md").write_text("notes", encoding="utf-8")
            (temp_path / "7.md").write_text("paper body", encoding="utf-8")

            selected = orchestration_module._find_primary_paper_markdown_file(
                str(temp_path)
            )

            self.assertEqual(Path(selected).name, "7.md")
        finally:
            if temp_path.exists():
                shutil.rmtree(temp_path)

    def test_should_refresh_initial_plan_when_user_image_context_is_newer(self):
        temp_path = ROOT / "tests" / "_tmp_plan_refresh"
        if temp_path.exists():
            shutil.rmtree(temp_path)
        temp_path.mkdir(parents=True, exist_ok=True)
        try:
            initial_plan_path = temp_path / "initial_plan.txt"
            user_image_context_path = temp_path / "user_image_context.json"
            initial_plan_path.write_text("old plan", encoding="utf-8")
            user_image_context_path.write_text("{}", encoding="utf-8")
            now = time.time()
            os.utime(initial_plan_path, (now - 10, now - 10))
            os.utime(user_image_context_path, (now, now))

            self.assertTrue(
                orchestration_module._should_refresh_initial_plan(
                    str(initial_plan_path), str(user_image_context_path)
                )
            )
        finally:
            if temp_path.exists():
                shutil.rmtree(temp_path)

if __name__ == "__main__":
    unittest.main()
