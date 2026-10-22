import json
from typing import Any

import httpx
import ollama
import pytest
from pydantic import BaseModel

from content_agents.config import OllamaSettings
from content_agents.llm.budget import BudgetExceededError, TokenBudget, Usage
from content_agents.llm.client import LLMClient, LLMTruncatedError, LLMUnavailableError

HOST = "http://ollama.test"
BULK = "llama3.2:1b"
WRITING = "qwen3-vl:8b"
MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "Write a brief."}]
BRIEF_JSON = '{"topic": "pgvector", "keywords": ["vector search"]}'


class Brief(BaseModel):
    topic: str
    keywords: list[str]


def chat_response(
    content: str,
    *,
    model: str = WRITING,
    thinking: str | None = None,
    done_reason: str = "stop",
    prompt_tokens: int = 1200,
    output_tokens: int = 300,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking is not None:
        message["thinking"] = thinking
    return {
        "model": model,
        "created_at": "2026-10-22T12:00:00Z",
        "message": message,
        "done": True,
        "done_reason": done_reason,
        "total_duration": 2_000_000_000,
        "prompt_eval_count": prompt_tokens,
        "eval_count": output_tokens,
    }


class FakeOllama:
    """Serves queued /api/chat responses to the real ollama client and records request bodies."""

    def __init__(self, *responses: httpx.Response | dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)


def make_llm(
    api: FakeOllama, *, max_tokens: int = 100_000, think: bool = True
) -> tuple[LLMClient, TokenBudget]:
    settings = OllamaSettings(
        _env_file=None,
        host=HOST,
        bulk_model=BULK,
        writing_model=WRITING,
        num_ctx=32_768,
        keep_alive="30m",
        think=think,
    )
    client = ollama.AsyncClient(host=HOST, transport=httpx.MockTransport(api.handler))
    budget = TokenBudget(max_tokens=max_tokens)
    return LLMClient(client, settings, budget), budget


async def test_parse_returns_validated_output_and_charges_tokens() -> None:
    api = FakeOllama(chat_response(BRIEF_JSON, model=BULK))
    llm, budget = make_llm(api)

    result = await llm.parse("fact_sheet", system="sys", messages=MESSAGES, output_format=Brief)

    assert result.output == Brief(topic="pgvector", keywords=["vector search"])
    assert result.model == BULK
    assert result.usage == Usage(prompt_tokens=1200, output_tokens=300)
    assert budget.used_tokens == 1500


async def test_bulk_request_shape() -> None:
    api = FakeOllama(chat_response(BRIEF_JSON, model=BULK))
    llm, _ = make_llm(api)

    await llm.parse(
        "fact_sheet", system="sys", messages=MESSAGES, output_format=Brief, max_tokens=4096
    )

    body = api.bodies[0]
    assert body["model"] == BULK
    assert body["stream"] is False
    assert body["think"] is False
    assert body["format"] == Brief.model_json_schema()
    assert body["options"] == {"num_ctx": 32_768, "num_predict": 4096, "temperature": 0.1}
    assert body["keep_alive"] == "30m"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == MESSAGES[0]


async def test_writer_uses_writing_model_with_thinking_and_no_format() -> None:
    api = FakeOllama(chat_response("Draft text"))
    llm, _ = make_llm(api)

    await llm.text("writer", system="sys", messages=MESSAGES)

    body = api.bodies[0]
    assert body["model"] == WRITING
    assert body["think"] is True
    assert "format" not in body


async def test_thinking_switched_off_omits_think_field() -> None:
    api = FakeOllama(chat_response("Draft text"))
    llm, _ = make_llm(api, think=False)

    await llm.text("writer", system="sys", messages=MESSAGES)

    assert "think" not in api.bodies[0]


async def test_text_returns_content_without_thinking() -> None:
    api = FakeOllama(chat_response("Final answer.", thinking="Let me reason first..."))
    llm, _ = make_llm(api)

    result = await llm.text("writer", system="sys", messages=MESSAGES)

    assert result.output == "Final answer."


async def test_length_stop_raises_truncated_after_charging() -> None:
    api = FakeOllama(chat_response('{"topic": "pg', done_reason="length"))
    llm, budget = make_llm(api)

    with pytest.raises(LLMTruncatedError):
        await llm.parse("outline", system="sys", messages=MESSAGES, output_format=Brief)

    assert budget.used_tokens == 1500


async def test_exhausted_budget_blocks_call_before_any_request() -> None:
    api = FakeOllama()
    llm, budget = make_llm(api, max_tokens=1000)
    budget.charge(Usage(prompt_tokens=1000))

    with pytest.raises(BudgetExceededError):
        await llm.text("writer", system="sys", messages=MESSAGES)

    assert api.bodies == []


async def test_unreachable_server_raises_unavailable_with_host() -> None:
    api = FakeOllama(httpx.ConnectError("connection refused"))
    llm, _ = make_llm(api)

    with pytest.raises(LLMUnavailableError, match=HOST):
        await llm.text("writer", system="sys", messages=MESSAGES)


async def test_missing_model_raises_unavailable_with_pull_hint() -> None:
    not_found = httpx.Response(404, json={"error": f"model '{WRITING}' not found"})
    api = FakeOllama(not_found)
    llm, _ = make_llm(api)

    with pytest.raises(LLMUnavailableError, match=f"ollama pull {WRITING}"):
        await llm.text("writer", system="sys", messages=MESSAGES)


async def test_other_server_errors_propagate() -> None:
    api = FakeOllama(httpx.Response(500, json={"error": "out of memory"}))
    llm, _ = make_llm(api)

    with pytest.raises(ollama.ResponseError):
        await llm.text("writer", system="sys", messages=MESSAGES)
