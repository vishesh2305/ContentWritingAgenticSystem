import pytest
from pydantic import ValidationError

from content_agents.graph.state import (
    ContentBrief,
    DetectorResult,
    Patch,
    PipelineState,
    Source,
    Span,
)


def make_brief(**overrides: object) -> ContentBrief:
    fields: dict[str, object] = {
        "topic": "pgvector",
        "refined_prompt": "Explain pgvector for backend engineers",
        "search_intent": "informational",
        "audience": "backend engineers",
        "tone": "conversational",
        "target_word_count": 2000,
        "primary_keyword": "pgvector",
        "secondary_keywords": ["vector search"],
        "questions_to_answer": ["What is pgvector?"],
        "locale": "en-US",
        "format": "guide",
    }
    fields.update(overrides)
    return ContentBrief.model_validate(fields)


def test_span_end_must_be_after_start() -> None:
    with pytest.raises(ValidationError):
        Span(start=10, end=10)


def test_span_start_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        Span(start=-1, end=5)


def test_source_credibility_must_be_between_0_and_1() -> None:
    with pytest.raises(ValidationError):
        Source(
            url="https://example.com/post",
            title="Post",
            domain="example.com",
            credibility=1.5,
            is_competitor=False,
            markdown="# Post",
        )


def test_detector_probability_must_be_between_0_and_1() -> None:
    with pytest.raises(ValidationError):
        DetectorResult(detector="gptzero", ai_probability=-0.1)


def test_patch_original_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        Patch(span=Span(start=0, end=1), original="", replacement="x", reason="r")


def test_brief_word_count_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_brief(target_word_count=0)


def test_brief_rejects_unknown_search_intent() -> None:
    with pytest.raises(ValidationError):
        make_brief(search_intent="vibes")


def test_pipeline_state_starts_empty() -> None:
    state = PipelineState(job_id="job-1", raw_prompt="Write about pgvector")
    assert state.sources == []
    assert state.humanize_round == 0
    assert state.paid_detector_checks == 0
    assert state.cost_usd == 0.0
    assert state.final is None


def test_pipeline_state_lists_are_not_shared_between_instances() -> None:
    first = PipelineState(job_id="a", raw_prompt="x")
    second = PipelineState(job_id="b", raw_prompt="y")
    first.protected_spans.append(Span(start=0, end=3))
    assert second.protected_spans == []
