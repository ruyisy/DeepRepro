"""Shared OpenAI-compatible client for direct agents and mcp-agent planning."""

import asyncio
from time import monotonic
from urllib.parse import urlparse

from openai import AsyncOpenAI as SDKAsyncOpenAI

from utils.run_metrics import current_metrics


class AsyncOpenAI(SDKAsyncOpenAI):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", 180.0)
        kwargs.setdefault("max_retries", 1)
        super().__init__(*args, **kwargs)
        create = self.chat.completions.create

        async def measured_create(*call_args, **call_kwargs):
            # The existing agent histories do not preserve reasoning_content.
            # Explicit non-thinking mode is required for DeepSeek tool loops.
            if urlparse(str(self.base_url)).hostname == "api.deepseek.com":
                extra = dict(call_kwargs.get("extra_body") or {})
                extra.setdefault("thinking", {"type": "disabled"})
                call_kwargs["extra_body"] = extra
            metrics = current_metrics.get()
            started = monotonic()
            stage = metrics.stage if metrics else "unknown"
            try:
                response = await create(*call_args, **call_kwargs)
            except (Exception, asyncio.CancelledError) as error:
                if metrics:
                    metrics.record(call_kwargs.get("model", "unknown"), stage,
                                   monotonic() - started, error=error)
                raise
            if metrics:
                metrics.record(call_kwargs.get("model", "unknown"), stage,
                               monotonic() - started, getattr(response, "usage", None))
            return response

        self.chat.completions.create = measured_create


def configure_mcp_openai_client():
    # mcp-agent 0.2.x constructs its client internally, without a client factory.
    # Adapt that single module rather than monkey-patching the OpenAI SDK globally.
    import mcp_agent.workflows.llm.augmented_llm_openai as module
    module.AsyncOpenAI = AsyncOpenAI
    return module.OpenAIAugmentedLLM
