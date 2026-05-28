import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
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


runtime_compat = load_module(
    "paper_to_code_runtime_compat_test_module",
    "utils/runtime_compat.py",
)


class RuntimeCompatTests(unittest.TestCase):
    def test_prepare_runtime_mcp_servers_normalizes_local_python_servers(self):
        config = SimpleNamespace(
            mcp=SimpleNamespace(
                servers={
                    "code-implementation": SimpleNamespace(
                        command="python",
                        args=["tools/code_implementation_server.py"],
                        env={"PYTHONPATH": "."},
                        cwd=None,
                    ),
                    "filesystem": SimpleNamespace(
                        command="npx",
                        args=["-y", "@modelcontextprotocol/server-filesystem", "."],
                        env={},
                        cwd=None,
                    ),
                }
            )
        )

        changed = runtime_compat.prepare_runtime_mcp_servers(
            config,
            project_root=ROOT,
            python_executable=r"C:\python-env\python.exe",
        )

        server = config.mcp.servers["code-implementation"]
        self.assertEqual(changed, ["code-implementation", "filesystem"])
        self.assertEqual(server.command, r"C:\python-env\python.exe")
        self.assertEqual(
            server.args[0],
            str((ROOT / "tools/code_implementation_server.py").resolve()),
        )
        self.assertEqual(server.cwd, str(ROOT.resolve()))
        self.assertEqual(server.env["PYTHONPATH"], str(ROOT.resolve()))
        filesystem_server = config.mcp.servers["filesystem"]
        self.assertEqual(filesystem_server.command, "npx")
        self.assertEqual(filesystem_server.cwd, str(ROOT.resolve()))
        self.assertEqual(
            filesystem_server.env["NPM_CONFIG_CACHE"],
            str(ROOT.resolve() / ".cache" / "npm"),
        )

    def test_configure_windows_event_loop_policy_sets_selector_policy(self):
        fake_policy_cls = type("FakeSelectorPolicy", (), {})
        with mock.patch.object(runtime_compat.sys, "platform", "win32"), mock.patch.object(
            runtime_compat.asyncio,
            "WindowsSelectorEventLoopPolicy",
            fake_policy_cls,
            create=True,
        ), mock.patch.object(
            runtime_compat.asyncio,
            "get_event_loop_policy",
            return_value=object(),
        ), mock.patch.object(runtime_compat.asyncio, "set_event_loop_policy") as mocked_set:
            changed = runtime_compat.configure_windows_event_loop_policy()

        self.assertTrue(changed)
        mocked_set.assert_called_once()
        self.assertIsInstance(mocked_set.call_args.args[0], fake_policy_cls)

    def test_configure_windows_event_loop_policy_skips_when_already_selector(self):
        fake_policy_cls = type("FakeSelectorPolicy", (), {})
        existing_policy = fake_policy_cls()
        with mock.patch.object(runtime_compat.sys, "platform", "win32"), mock.patch.object(
            runtime_compat.asyncio,
            "WindowsSelectorEventLoopPolicy",
            fake_policy_cls,
            create=True,
        ), mock.patch.object(
            runtime_compat.asyncio,
            "get_event_loop_policy",
            return_value=existing_policy,
        ), mock.patch.object(runtime_compat.asyncio, "set_event_loop_policy") as mocked_set:
            changed = runtime_compat.configure_windows_event_loop_policy()

        self.assertFalse(changed)
        mocked_set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
