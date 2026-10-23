"""Contracts shared by every agent."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from content_agents.llm.client import LLMResult

ModelT = TypeVar("ModelT", bound=BaseModel)
Messages = Sequence[Mapping[str, Any]]

# A node's partial update, merged into PipelineState by the graph.
StateUpdate = dict[str, Any]


class ChatLLM(Protocol):
    """What agents need from LLMClient; lets tests pass a fake."""

    async def text(
        self, agent: str, *, system: str, messages: Messages, max_tokens: int = ...
    ) -> LLMResult[str]: ...

    async def parse(
        self,
        agent: str,
        *,
        system: str,
        messages: Messages,
        output_format: type[ModelT],
        max_tokens: int = ...,
    ) -> LLMResult[ModelT]: ...
