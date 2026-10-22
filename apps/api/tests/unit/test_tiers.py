import pytest

from content_agents.llm.tiers import AGENT_CONFIGS, resolve

PLAN_AGENTS = {
    "intake",
    "brief",
    "research_planner",
    "fact_sheet",
    "competitor_summary",
    "outline",
    "writer",
    "claim_splitter",
    "fact_verifier",
    "fact_escalation",
    "search_optimizer",
    "humanizer",
    "benchmark_judge",
}


def test_every_plan_agent_has_a_config() -> None:
    assert set(AGENT_CONFIGS) == PLAN_AGENTS


def test_extraction_agents_use_bulk_tier_without_thinking() -> None:
    config = resolve("fact_sheet")
    assert config.tier == "bulk"
    assert config.think is False


def test_writer_uses_writing_tier_with_thinking() -> None:
    config = resolve("writer")
    assert config.tier == "writing"
    assert config.think is True


def test_humanizer_samples_hotter_than_extraction() -> None:
    assert resolve("humanizer").temperature > resolve("fact_sheet").temperature


@pytest.mark.parametrize("agent", sorted(PLAN_AGENTS))
def test_temperatures_are_in_a_sane_range(agent: str) -> None:
    assert 0.0 <= resolve(agent).temperature <= 1.0


def test_unknown_agent_raises() -> None:
    with pytest.raises(KeyError):
        resolve("not_an_agent")
