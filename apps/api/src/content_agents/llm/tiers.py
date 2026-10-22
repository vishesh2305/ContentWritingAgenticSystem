"""Per-agent model tier, thinking and sampling settings (Plan.md §4.2)."""

from dataclasses import dataclass
from typing import Literal

# "bulk" = extraction/classification; "writing" = drafting, editing and judging.
# Each tier maps to an Ollama model via OllamaSettings.
Tier = Literal["bulk", "writing"]


@dataclass(frozen=True)
class AgentLLMConfig:
    tier: Tier
    think: bool
    temperature: float


AGENT_CONFIGS: dict[str, AgentLLMConfig] = {
    "intake": AgentLLMConfig("bulk", think=False, temperature=0.2),
    "brief": AgentLLMConfig("bulk", think=False, temperature=0.3),
    "research_planner": AgentLLMConfig("bulk", think=False, temperature=0.3),
    "fact_sheet": AgentLLMConfig("bulk", think=False, temperature=0.1),
    "competitor_summary": AgentLLMConfig("bulk", think=False, temperature=0.2),
    "outline": AgentLLMConfig("writing", think=True, temperature=0.6),
    "writer": AgentLLMConfig("writing", think=True, temperature=0.7),
    "claim_splitter": AgentLLMConfig("bulk", think=False, temperature=0.1),
    "fact_verifier": AgentLLMConfig("bulk", think=False, temperature=0.1),
    "fact_escalation": AgentLLMConfig("writing", think=True, temperature=0.3),
    "search_optimizer": AgentLLMConfig("writing", think=False, temperature=0.4),
    # Higher temperature gives more varied word choice and rhythm in rewrites.
    "humanizer": AgentLLMConfig("writing", think=True, temperature=0.9),
    "benchmark_judge": AgentLLMConfig("writing", think=True, temperature=0.2),
}


def resolve(agent: str) -> AgentLLMConfig:
    return AGENT_CONFIGS[agent]
