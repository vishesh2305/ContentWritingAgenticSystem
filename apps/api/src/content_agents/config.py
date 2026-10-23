"""Runtime settings, read from the environment and `.env`."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from content_agents.llm.tiers import Tier


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_", env_file=".env", extra="ignore")

    host: str = "http://localhost:11434"
    # One model for both tiers by default: on a 16 GB machine, alternating between two
    # ~6 GB models makes Ollama reload weights on every switch.
    bulk_model: str = "qwen3-vl:8b-instruct"
    writing_model: str = "qwen3-vl:8b-instruct"
    # Per-agent thinking is only sent when this is on, and needs a thinking-capable model.
    # (qwen3-vl:8b ignores think=False and puts its whole answer in `thinking`.)
    think: bool = False
    # Keep identical across calls: a different num_ctx forces Ollama to reload the model.
    # Prompts longer than this are silently truncated by Ollama.
    num_ctx: int = Field(default=32_768, gt=0)
    keep_alive: str = "30m"

    def model_for(self, tier: Tier) -> str:
        return self.bulk_model if tier == "bulk" else self.writing_model


class ResearchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Optional: real Google rankings + "People also ask". Without it, DuckDuckGo (free).
    serper_api_key: str | None = None
    cache_path: Path = Path(".cache/content_agents.db")
    max_sources: int = Field(default=15, gt=0)
    user_agent: str = "ContentAgentsBot/0.1 (research crawler; respects robots.txt)"
