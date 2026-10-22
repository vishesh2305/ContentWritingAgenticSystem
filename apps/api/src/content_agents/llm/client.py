"""Single entry point for local LLM calls via Ollama: tier routing, token budget, errors."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from ollama import AsyncClient, ChatResponse, ResponseError
from pydantic import BaseModel

from content_agents.config import OllamaSettings
from content_agents.llm.budget import TokenBudget, Usage
from content_agents.llm.tiers import resolve

DEFAULT_MAX_TOKENS = 8_192

ModelT = TypeVar("ModelT", bound=BaseModel)
Messages = Sequence[Mapping[str, Any]]


class LLMUnavailableError(RuntimeError):
    """Ollama isn't running, or the configured model isn't pulled."""


class LLMTruncatedError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResult[T]:
    output: T
    model: str
    usage: Usage


class LLMClient:
    def __init__(self, ollama: AsyncClient, settings: OllamaSettings, budget: TokenBudget) -> None:
        self._ollama = ollama
        self._settings = settings
        self._budget = budget

    async def parse(
        self,
        agent: str,
        *,
        system: str,
        messages: Messages,
        output_format: type[ModelT],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult[ModelT]:
        response = await self._chat(
            agent, system, messages, max_tokens, json_schema=output_format.model_json_schema()
        )
        output = output_format.model_validate_json(response.message.content or "")
        return LLMResult(output, response.model or "", Usage.from_response(response))

    async def text(
        self,
        agent: str,
        *,
        system: str,
        messages: Messages,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult[str]:
        response = await self._chat(agent, system, messages, max_tokens)
        # Thinking arrives separately in message.thinking; only the answer is returned.
        return LLMResult(
            response.message.content or "", response.model or "", Usage.from_response(response)
        )

    async def _chat(
        self,
        agent: str,
        system: str,
        messages: Messages,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResponse:
        self._budget.ensure_available()
        config = resolve(agent)
        model = self._settings.model_for(config.tier)
        try:
            response = await self._ollama.chat(
                model=model,
                messages=[{"role": "system", "content": system}, *messages],
                format=json_schema,
                # None omits the field: non-thinking models reject `think` entirely.
                think=config.think if self._settings.think else None,
                options={
                    "num_ctx": self._settings.num_ctx,
                    "num_predict": max_tokens,
                    "temperature": config.temperature,
                },
                keep_alive=self._settings.keep_alive,
            )
        except ConnectionError as e:
            raise LLMUnavailableError(
                f"Ollama is not reachable at {self._settings.host}; start it with `ollama serve`"
            ) from e
        except ResponseError as e:
            if e.status_code == 404:
                raise LLMUnavailableError(
                    f"model {model!r} is not available; run `ollama pull {model}`"
                ) from e
            raise

        self._budget.charge(Usage.from_response(response))
        if response.done_reason == "length":
            raise LLMTruncatedError(f"{agent}: {model} stopped at num_predict={max_tokens}")
        return response
