"""Test doubles shared across test modules."""

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from content_agents.llm.budget import Usage
from content_agents.llm.client import LLMResult

ModelT = TypeVar("ModelT", bound=BaseModel)


class FakeLLM:
    """Stands in for LLMClient: returns queued replies and records every call."""

    def __init__(
        self, *, text_replies: Sequence[str] = (), parse_replies: Sequence[BaseModel] = ()
    ) -> None:
        self.text_replies = list(text_replies)
        self.parse_replies = list(parse_replies)
        self.calls: list[dict[str, Any]] = []

    async def text(
        self,
        agent: str,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int = 8_192,
    ) -> LLMResult[str]:
        self.calls.append({"agent": agent, "system": system, "messages": messages})
        return LLMResult(self.text_replies.pop(0), "fake-model", Usage())

    async def parse(
        self,
        agent: str,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        output_format: type[ModelT],
        max_tokens: int = 8_192,
    ) -> LLMResult[ModelT]:
        self.calls.append({"agent": agent, "system": system, "messages": messages})
        reply = self.parse_replies.pop(0)
        assert isinstance(reply, output_format)
        return LLMResult(reply, "fake-model", Usage())
