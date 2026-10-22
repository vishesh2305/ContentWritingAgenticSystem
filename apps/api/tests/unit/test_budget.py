from types import SimpleNamespace

import pytest

from content_agents.llm.budget import BudgetExceededError, TokenBudget, Usage


def test_usage_from_response_reads_ollama_counters() -> None:
    response = SimpleNamespace(prompt_eval_count=1200, eval_count=300)
    usage = Usage.from_response(response)
    assert usage == Usage(prompt_tokens=1200, output_tokens=300)
    assert usage.total_tokens == 1500


def test_usage_from_response_treats_missing_counters_as_zero() -> None:
    response = SimpleNamespace(prompt_eval_count=None, eval_count=None)
    assert Usage.from_response(response) == Usage()


def test_budget_accumulates_tokens() -> None:
    budget = TokenBudget(max_tokens=10_000)
    budget.charge(Usage(prompt_tokens=2_000, output_tokens=500))
    budget.charge(Usage(prompt_tokens=1_000, output_tokens=500))
    assert budget.used_tokens == 4_000
    assert budget.remaining_tokens == 6_000


def test_budget_allows_calls_while_under_limit() -> None:
    budget = TokenBudget(max_tokens=10_000)
    budget.charge(Usage(prompt_tokens=9_999))
    budget.ensure_available()


def test_budget_blocks_calls_once_used_up() -> None:
    budget = TokenBudget(max_tokens=10_000)
    budget.charge(Usage(prompt_tokens=10_000))
    with pytest.raises(BudgetExceededError):
        budget.ensure_available()


def test_budget_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TokenBudget(max_tokens=0)
