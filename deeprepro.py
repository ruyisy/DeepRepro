#!/usr/bin/env python3
"""
DeepRepro local launcher.

Usage:
    python ./deeprepro.py --local
"""

from __future__ import annotations

import os
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


BACKEND_PORT = 8000
FRONTEND_PORT = 5173

_backend_process: subprocess.Popen | None = None
_frontend_process: subprocess.Popen | None = None


def get_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def print_banner() -> None:
    banner = r"""
+------------------------------------------------------------+
|                                                            |
|                      Welcome to DeepRepro                  |
|                                                            |
|        Automatic Paper-to-Code Reproduction Console        |
|                                                            |
|        Deep planning / Agentic execution / Memory flow     |
|                                                            |
|        Local UI:  http://localhost:5173                    |
|        API:       http://localhost:8000                    |
|                                                            |
+------------------------------------------------------------+
"""
    print(banner)


def check_dependencies() -> bool:
    import importlib.util
    import shutil

    print("Checking local runtime dependencies...")
    missing_python: list[str] = []
    missing_system: list[str] = []

    for module_name, package_name in [
        ("fastapi", "fastapi>=0.104.0"),
        ("uvicorn", "uvicorn>=0.24.0"),
        ("yaml", "pyyaml>=6.0"),
        ("pydantic_settings", "pydantic-settings>=2.0.0"),
    ]:
        if importlib.util.find_spec(module_name) is None:
            missing_python.append(package_name)

    if not (shutil.which("node") or shutil.which("node.exe")):
        missing_system.append("Node.js")
    if not (shutil.which("npm") or shutil.which("npm.cmd")):
        missing_system.append("npm")

    if missing_python:
        print("Missing Python dependencies:")
        for dependency in missing_python:
            print(f"  - {dependency}")
        print(f"Install with: {sys.executable} -m pip install {' '.join(missing_python)}")

    if missing_system:
        print("Missing system dependencies:")
        for dependency in missing_system:
            print(f"  - {dependency}")
        print("Install Node.js from https://nodejs.org/ and retry.")

    if missing_python or missing_system:
        return False

    print("All local runtime dependencies are available.")
    return True


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("localhost", port)) == 0


def kill_process_on_port(port: int) -> None:
    try:
        if get_platform() == "windows":
            result = subprocess.run(
                f"netstat -ano | findstr :{port}",
                capture_output=True,
                text=True,
                shell=True,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[-1].isdigit():
                    subprocess.run(
                        f"taskkill /F /PID {parts[-1]}",
                        shell=True,
                        capture_output=True,
                    )
        else:
            result = subprocess.run(
                f"lsof -ti :{port}", capture_output=True, text=True, shell=True
            )
            for pid in result.stdout.strip().splitlines():
                if pid.isdigit():
                    os.kill(int(pid), signal.SIGKILL)
    except Exception as exc:
        print(f"Warning: could not clean port {port}: {exc}")


def cleanup_ports() -> None:
    for port in [BACKEND_PORT, FRONTEND_PORT]:
        if is_port_in_use(port):
            print(f"Port {port} is in use; stopping the existing process.")
            kill_process_on_port(port)
            time.sleep(1)


def install_frontend_deps(frontend_dir: Path) -> None:
    if (frontend_dir / "node_modules").exists():
        return

    print("Installing frontend dependencies...")
    npm_cmd = "npm.cmd" if get_platform() == "windows" else "npm"
    subprocess.run(
        [npm_cmd, "install"],
        cwd=frontend_dir,
        check=True,
        shell=(get_platform() == "windows"),
    )


def start_backend(backend_dir: Path) -> bool:
    global _backend_process

    print("Starting DeepRepro backend...")
    if get_platform() == "windows":
        _backend_process = subprocess.Popen(
            f'"{sys.executable}" -m uvicorn main:app --host 0.0.0.0 --port {BACKEND_PORT} --reload',
            cwd=backend_dir,
            shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        _backend_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(BACKEND_PORT),
                "--reload",
            ],
            cwd=backend_dir,
            start_new_session=True,
        )

    time.sleep(2)
    if _backend_process.poll() is None:
        print(f"Backend ready: http://localhost:{BACKEND_PORT}")
        return True

    print("Backend failed to start.")
    return False


def start_frontend(frontend_dir: Path) -> bool:
    global _frontend_process

    print("Starting DeepRepro frontend...")
    npm_cmd = "npm.cmd" if get_platform() == "windows" else "npm"
    if get_platform() == "windows":
        _frontend_process = subprocess.Popen(
            f"{npm_cmd} run dev",
            cwd=frontend_dir,
            shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        _frontend_process = subprocess.Popen(
            [npm_cmd, "run", "dev"],
            cwd=frontend_dir,
            start_new_session=True,
        )

    time.sleep(3)
    if _frontend_process.poll() is None:
        print(f"Frontend ready: http://localhost:{FRONTEND_PORT}")
        return True

    print("Frontend failed to start.")
    return False


def stop_process(name: str, process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return

    try:
        if get_platform() == "windows":
            subprocess.run(
                f"taskkill /F /T /PID {process.pid}",
                shell=True,
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
        print(f"{name} stopped.")
    except Exception:
        process.kill()
        print(f"{name} killed.")


def cleanup_processes() -> None:
    print("\nStopping DeepRepro services...")
    stop_process("Backend", _backend_process)
    stop_process("Frontend", _frontend_process)
    for port in [BACKEND_PORT, FRONTEND_PORT]:
        if is_port_in_use(port):
            kill_process_on_port(port)


def launch_local() -> None:
    print_banner()
    current_dir = Path(__file__).resolve().parent
    ui_dir = current_dir / "ui"
    backend_dir = ui_dir / "backend"
    frontend_dir = ui_dir / "frontend"

    if not backend_dir.exists() or not frontend_dir.exists():
        print("DeepRepro UI directories were not found.")
        print(f"Expected backend:  {backend_dir}")
        print(f"Expected frontend: {frontend_dir}")
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    try:
        cleanup_ports()
        install_frontend_deps(frontend_dir)
        if not start_backend(backend_dir):
            sys.exit(1)
        if not start_frontend(frontend_dir):
            cleanup_processes()
            sys.exit(1)

        print("\nDeepRepro is running.")
        print(f"  Frontend: http://localhost:{FRONTEND_PORT}")
        print(f"  Backend:  http://localhost:{BACKEND_PORT}")
        print("Press Ctrl+C to stop all services.\n")

        while True:
            if _backend_process and _backend_process.poll() is not None:
                print("Backend process exited unexpectedly.")
                break
            if _frontend_process and _frontend_process.poll() is not None:
                print("Frontend process exited unexpectedly.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("")
    finally:
        cleanup_processes()
        print("DeepRepro stopped.")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] != "--local":
        print("Usage: python ./deeprepro.py --local")
        sys.exit(1)
    launch_local()


if __name__ == "__main__":
    main()

