"""Per-run, provider-reported usage. Never store prompts, responses or secrets."""

from contextvars import ContextVar
import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Any


@dataclass
class RunMetrics:
    started: float = field(default_factory=monotonic)
    ended: float | None = None
    stage: str = "workspace"
    stage_started: float = field(default_factory=monotonic)
    stage_seconds: dict[str, float] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    elapsed_before: float = 0
    on_update: Any = field(default=None, repr=False)

    @classmethod
    def resume(cls, snapshot, requests):
        return cls(elapsed_before=snapshot.get("elapsed_seconds", 0),
                   stage_seconds=dict(snapshot.get("stage_seconds", {})),
                   requests=list(requests))

    def set_stage(self, stage: str):
        now = monotonic()
        self.stage_seconds[self.stage] = self.stage_seconds.get(self.stage, 0) + now - self.stage_started
        self.stage, self.stage_started = stage, now

    def finish(self):
        if self.ended is None:
            self.set_stage(self.stage)
            self.ended = monotonic()

    def record(self, model: str, stage: str, seconds: float, usage=None, error=None):
        data = usage.model_dump() if hasattr(usage, "model_dump") else (usage or {})
        details = data.get("prompt_tokens_details") or {}
        self.requests.append({
            "model": model, "stage": stage, "seconds": round(seconds, 3),
            "status": "cancelled" if isinstance(error, asyncio.CancelledError) else ("error" if error else "success"),
            "error_type": type(error).__name__ if error else None,
            "usage_reported": bool(data),
            "prompt_tokens": data.get("prompt_tokens", 0),
            "completion_tokens": data.get("completion_tokens", 0),
            "total_tokens": data.get("total_tokens", 0),
            "cached_tokens": data.get("prompt_cache_hit_tokens", details.get("cached_tokens", 0)),
        })
        if self.on_update:
            self.on_update()

    def snapshot(self):
        stages = dict(self.stage_seconds)
        if self.ended is None:
            stages[self.stage] = stages.get(self.stage, 0) + monotonic() - self.stage_started
        return {
            "elapsed_seconds": round(self.elapsed_before + (self.ended or monotonic()) - self.started, 2),
            "llm_calls": len(self.requests),
            "failed_calls": sum(r["status"] == "error" for r in self.requests),
            "cancelled_calls": sum(r["status"] == "cancelled" for r in self.requests),
            "usage_reported_calls": sum(r["usage_reported"] for r in self.requests),
            "prompt_tokens": sum(r["prompt_tokens"] for r in self.requests),
            "completion_tokens": sum(r["completion_tokens"] for r in self.requests),
            "total_tokens": sum(r["total_tokens"] for r in self.requests),
            "cached_tokens": sum(r["cached_tokens"] for r in self.requests),
            "llm_seconds": round(sum(r["seconds"] for r in self.requests), 2),
            "stage_seconds": {k: round(v, 2) for k, v in stages.items()},
            "models": sorted({r["model"] for r in self.requests}),
            "scope": "OpenAI-compatible chat completions; API-reported usage only",
        }


current_metrics: ContextVar[RunMetrics | None] = ContextVar("deeprepro_metrics", default=None)
