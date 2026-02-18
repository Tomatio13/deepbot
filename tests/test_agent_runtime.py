from __future__ import annotations

import asyncio
import threading
import time

import pytest

from deepbot.agent.runtime import (
    AgentRequest,
    AgentRuntime,
    ImageAttachment,
    _patch_openai_image_content_formatter,
)


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


@pytest.mark.asyncio
async def test_agent_runtime_serializes_concurrent_calls() -> None:
    state_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def blocking_agent(_: str) -> str:
        nonlocal in_flight, max_in_flight
        with state_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)
        with state_lock:
            in_flight -= 1
        return "done"

    runtime = AgentRuntime(agent_callable=blocking_agent, timeout_seconds=1)

    await asyncio.gather(
        runtime.generate_reply(AgentRequest(session_id="s1", context=[])),
        runtime.generate_reply(AgentRequest(session_id="s1", context=[])),
    )

    assert max_in_flight == 1


def test_build_prompt_skips_empty_context_messages() -> None:
    prompt = AgentRuntime._build_prompt(
        AgentRequest(
            session_id="s1",
            context=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "   "},
            ],
        )
    )

    assert "[user] hello" in prompt
    assert "[assistant]" not in prompt


def test_build_model_input_includes_image_blocks_when_attachments_present() -> None:
    model_input = AgentRuntime._build_model_input(
        AgentRequest(
            session_id="s1",
            context=[{"role": "user", "content": "cat image"}],
            image_attachments=(ImageAttachment(format="png", data=b"PNGDATA"),),
        )
    )

    assert isinstance(model_input, list)
    assert model_input[0]["role"] == "user"
    image_blocks = [b for b in model_input[0]["content"] if "image" in b]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image"]["format"] == "png"


def test_openai_image_formatter_patch_removes_detail_and_format() -> None:
    class DummyOpenAIModel:
        @classmethod
        def format_request_message_content(cls, content, **kwargs):
            return {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,AAA",
                    "detail": "auto",
                    "format": "image/jpeg",
                },
            }

    _patch_openai_image_content_formatter(DummyOpenAIModel)
    formatted = DummyOpenAIModel.format_request_message_content({"image": {}})
    assert formatted["type"] == "image_url"
    assert formatted["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "detail" not in formatted["image_url"]
    assert "format" not in formatted["image_url"]


def test_openai_image_formatter_patch_normalizes_empty_text_block() -> None:
    class DummyOpenAIModel:
        @classmethod
        def format_request_message_content(cls, content, **kwargs):
            return {"type": "text", "text": ""}

    _patch_openai_image_content_formatter(DummyOpenAIModel)
    formatted = DummyOpenAIModel.format_request_message_content({"text": ""})
    assert formatted["type"] == "text"
    assert formatted["text"] == " "


@pytest.mark.asyncio
async def test_agent_runtime_stream_async_sends_progress_and_result() -> None:
    class StreamingAgent:
        def __call__(self, _: str) -> str:
            return "fallback"

        async def stream_async(self, _: str):
            yield {"current_tool_use": {"name": "firecrawl_search"}}
            yield {"data": "途中テキスト"}
            yield {"result": "最終結果"}

    runtime = AgentRuntime(agent_callable=StreamingAgent(), timeout_seconds=1)
    progress: list[str] = []

    async def on_progress(text: str) -> None:
        progress.append(text)

    result = await runtime.generate_reply(
        AgentRequest(
            session_id="s1",
            context=[{"role": "user", "content": "hello"}],
            progress_callback=on_progress,
        )
    )

    assert result == "最終結果"
    assert progress == ["調査を続けています…（firecrawl_search）"]


@pytest.mark.asyncio
async def test_agent_runtime_stream_async_timeout_returns_partial_text() -> None:
    class SlowStreamingAgent:
        def __call__(self, _: str) -> str:
            return "fallback"

        async def stream_async(self, _: str):
            yield {"data": "途中結果"}
            await asyncio.sleep(0.2)
            yield {"result": "完了結果"}

    runtime = AgentRuntime(agent_callable=SlowStreamingAgent(), timeout_seconds=0.05)

    result = await runtime.generate_reply(
        AgentRequest(session_id="s1", context=[{"role": "user", "content": "hello"}])
    )
    assert "途中結果" in result
    assert "ここまでの結果" in result
