"""Agent 2: turn the request into a ContentBrief and VoiceProfile (Plan.md §7)."""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from content_agents.agents.base import ChatLLM, StateUpdate
from content_agents.graph.state import ContentBrief, PipelineState, VoiceProfile, VoiceStyle

__all__ = ["BriefAgent", "BriefDraft", "VoiceStyle", "pick_excerpts"]

BRIEF_MAX_TOKENS = 2_048
MAX_EXCERPTS = 3
# Long enough to show sentence rhythm, short enough to keep the prompt small.
MIN_EXCERPT_WORDS = 40
MAX_EXCERPT_WORDS = 250

SYSTEM = """You are a content strategist. Turn a writing request into a precise content \
brief and a voice profile.

Brief:
- Infer search intent, audience, tone, format and a realistic word count.
- primary_keyword is the phrase a reader would actually type into a search engine; \
secondary_keywords are close variants and related terms.
- questions_to_answer are the concrete questions this audience needs answered.

Voice:
- If writing samples are given, describe the author's own voice from them: point of \
view, formality and recurring phrases.
- Otherwise choose a persona with first-hand experience of the topic.
- avoid_phrases are stock phrases that would sound generic in this voice.

clarifying_questions: leave empty unless the topic or audience genuinely cannot be \
inferred. Ask at most 3."""


class BriefDraft(BaseModel):
    brief: ContentBrief
    voice: VoiceStyle
    clarifying_questions: list[str] = Field(default_factory=list, max_length=3)


def pick_excerpts(samples: Sequence[str]) -> list[str]:
    """Up to three paragraphs from the user's samples that show their rhythm."""
    excerpts: list[str] = []
    for sample in samples:
        for raw in sample.split("\n\n"):
            paragraph = raw.strip()
            if MIN_EXCERPT_WORDS <= len(paragraph.split()) <= MAX_EXCERPT_WORDS:
                excerpts.append(paragraph)
                if len(excerpts) == MAX_EXCERPTS:
                    return excerpts
    return excerpts


class BriefAgent:
    name = "brief"

    def __init__(self, llm: ChatLLM) -> None:
        self._llm = llm

    async def run(self, state: PipelineState) -> StateUpdate:
        excerpts = pick_excerpts(state.writing_samples)
        result = await self._llm.parse(
            self.name,
            system=SYSTEM,
            messages=[{"role": "user", "content": _request_message(state, excerpts)}],
            output_format=BriefDraft,
            max_tokens=BRIEF_MAX_TOKENS,
        )
        draft = result.output
        voice = VoiceProfile.model_validate(
            {
                **draft.voice.model_dump(),
                "sample_excerpts": excerpts,
                "experience_notes": state.experience_notes,
            }
        )
        return {
            "brief": draft.brief,
            "voice": voice,
            "clarifying_questions": draft.clarifying_questions,
        }


def _request_message(state: PipelineState, excerpts: Sequence[str]) -> str:
    parts = [f"Request:\n{state.raw_prompt}"]
    if state.ingested_context:
        parts.append(f"Attached material:\n{state.ingested_context}")
    if excerpts:
        parts.append("Writing samples (the author's own words):\n" + "\n---\n".join(excerpts))
    if state.experience_notes:
        notes = "\n".join(f"- {note}" for note in state.experience_notes)
        parts.append(f"Experience notes from the author:\n{notes}")
    return "\n\n".join(parts)
