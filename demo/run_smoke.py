"""Run a bounded PaperBench excerpt through the local demo API.

Start DeepRepro first, then: .venv/bin/python demo/run_smoke.py
"""
import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://media.githubusercontent.com/media/openai/frontier-evals/main/project/paperbench/data/papers/bam/paper.md"
SCOPE = (
    "CIKM live demo only: implement a tiny 2D Gaussian BaM numerical smoke test in NumPy. "
    "Deliberately scope down the paper: at most 3 files (bam.py, demo.py, README.md), "
    "no datasets, downloads, training, plotting or extra infrastructure. demo.py must "
    "finish in under 5 seconds and print finite mean/covariance, positive minimum "
    "eigenvalue and elapsed time. README must say this is a pipeline smoke test, not "
    "full paper replication. Keep plans concise and implement in one batch. Do not "
    "access authors code."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["raw_fast", "raw_deepplan"], default="raw_deepplan")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    with httpx.Client(timeout=60) as client:
        client.get("http://127.0.0.1:8000/health").raise_for_status()
        paper = ROOT / "demo" / "paperbench-bam.md"
        if not paper.exists():
            response = client.get(SOURCE, follow_redirects=True)
            response.raise_for_status()
            paper.write_text(response.text, encoding="utf-8")
        excerpt = ROOT / "demo" / "paperbench-bam-excerpt.md"
        excerpt.write_text(
            "PaperBench BaM excerpt: abstract and Sections 1-3.1. Pipeline test only.\n\n"
            + paper.read_text(encoding="utf-8").split(r"\subsection*{3.2.")[0],
            encoding="utf-8",
        )
        with excerpt.open("rb") as file:
            upload = client.post("http://127.0.0.1:8000/api/v1/files/upload", files={"file": (excerpt.name, file, "text/markdown")})
        upload.raise_for_status()
        response = client.post("http://127.0.0.1:8000/api/v1/workflows/paper-to-code", json={
            "input_source": upload.json()["path"], "input_type": "file",
            "workflow_mode": args.mode, "supplementary_requirements": SCOPE,
        })
        response.raise_for_status()
        task_id = response.json()["task_id"]
        print(f"Open: http://127.0.0.1:5173/?task={task_id}", flush=True)
        started, last = time.monotonic(), ""
        while True:
            response = client.get(f"http://127.0.0.1:8000/api/v1/workflows/status/{task_id}")
            response.raise_for_status()
            run = response.json()
            state = f"{run['status']} {run['progress']}% {run['message']}"
            if state != last:
                print(state, flush=True)
                last = state
            if run["status"] in {"completed", "error", "cancelled", "paused"}:
                (ROOT / "demo" / "last-run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
                print(json.dumps(run.get("metrics", {}), indent=2))
                raise SystemExit(0 if run["status"] == "completed" else 1)
            if time.monotonic() - started > args.timeout:
                client.post(f"http://127.0.0.1:8000/api/v1/workflows/pause/{task_id}").raise_for_status()
                raise SystemExit("Smoke test timed out; pause requested; resume from the UI")
            time.sleep(2)


if __name__ == "__main__":
    main()
