"""Token usage and the per-job token budget.

Local models cost nothing per call, so the budget caps tokens instead of dollars;
it stops a runaway revision or humanize loop from tying up the machine.
"""

from dataclasses import dataclass
from typing import Any, Self


class BudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    # Includes thinking tokens when the model reasons before answering.
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    @classmethod
    def from_response(cls, response: Any) -> Self:
        return cls(
            prompt_tokens=response.prompt_eval_count or 0,
            output_tokens=response.eval_count or 0,
        )


class TokenBudget:
    def __init__(self, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens
        self.used_tokens = 0

    @property
    def remaining_tokens(self) -> int:
        return self.max_tokens - self.used_tokens

    def charge(self, usage: Usage) -> None:
        self.used_tokens += usage.total_tokens

    def ensure_available(self) -> None:
        if self.used_tokens >= self.max_tokens:
            raise BudgetExceededError(
                f"job token budget {self.max_tokens:,} used up ({self.used_tokens:,} used)"
            )
