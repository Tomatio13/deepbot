from __future__ import annotations

import asyncio

import pytest

from deepbot.agent.runtime import AgentRequest, AgentRuntime


@pytest.mark.asyncio
async def test_agent_runtime_returns_text() -> None:
    runtime = AgentRuntime(agent_callable=lambda prompt: f"ok:{prompt[:5]}", timeout_seconds=1)

    result = await runtime.generate_reply(
        AgentRequest(session_id="s1", context=[{"role": "user", "content": "hello"}])
    )

    assert result.startswith("ok:")


@pytest.mark.asyncio
async def test_agent_runtime_timeout() -> None:
    def slow_agent(_: str) -> str:
        import time

        time.sleep(0.2)
        return "done"

    runtime = AgentRuntime(agent_callable=slow_agent, timeout_seconds=0.05)

    with pytest.raises(asyncio.TimeoutError):
        await runtime.generate_reply(AgentRequest(session_id="s1", context=[]))
