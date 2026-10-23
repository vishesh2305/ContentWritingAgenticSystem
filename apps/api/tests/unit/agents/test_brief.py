from collections.abc import Sequence

from fakes import FakeLLM

from content_agents.agents.brief import BriefAgent, BriefDraft, VoiceStyle, pick_excerpts
from content_agents.graph.state import ContentBrief, PipelineState
from content_agents.llm.schema import find_unbounded_arrays


def words(n: int, word: str = "word") -> str:
    return " ".join([word] * n)


LONG_A = words(50, "alpha")
LONG_B = words(60, "bravo")
LONG_C = words(45, "charlie")
LONG_D = words(70, "delta")
SAMPLE = "\n\n".join([LONG_A, "Too short to teach rhythm.", LONG_B, LONG_C, LONG_D])
NOTE = "We migrated 40M vectors to pgvector last year."


def make_brief() -> ContentBrief:
    return ContentBrief(
        topic="pgvector",
        refined_prompt="A practical guide to pgvector for backend engineers",
        search_intent="informational",
        audience="backend engineers",
        tone="conversational",
        target_word_count=2000,
        primary_keyword="pgvector",
        secondary_keywords=["vector search", "postgres embeddings"],
        questions_to_answer=["When should I use HNSW?"],
        format="guide",
    )


def make_draft(questions: Sequence[str] = ()) -> BriefDraft:
    return BriefDraft(
        brief=make_brief(),
        voice=VoiceStyle(
            persona="backend engineer who has run pgvector in production",
            pov="first_person",
            formality="conversational",
            signature_phrases=["here's the catch"],
            avoid_phrases=["delve"],
        ),
        clarifying_questions=list(questions),
    )


def make_state(**overrides: object) -> PipelineState:
    fields: dict[str, object] = {"job_id": "job", "raw_prompt": "Write about pgvector"}
    fields.update(overrides)
    return PipelineState.model_validate(fields)


async def test_returns_brief_and_voice_profile() -> None:
    llm = FakeLLM(parse_replies=[make_draft()])
    state = make_state(writing_samples=[SAMPLE], experience_notes=[NOTE])

    update = await BriefAgent(llm).run(state)

    assert update["brief"] == make_brief()
    voice = update["voice"]
    assert voice.persona == "backend engineer who has run pgvector in production"
    assert voice.avoid_phrases == ["delve"]
    assert voice.experience_notes == [NOTE]
    assert voice.sample_excerpts == pick_excerpts([SAMPLE])
    assert update["clarifying_questions"] == []


async def test_prompt_includes_request_context_samples_and_notes() -> None:
    llm = FakeLLM(parse_replies=[make_draft()])
    state = make_state(
        ingested_context="## pdf: bench.pdf\nHNSW was 3x faster than IVF.",
        writing_samples=[SAMPLE],
        experience_notes=[NOTE],
    )

    await BriefAgent(llm).run(state)

    call = llm.calls[0]
    content = call["messages"][0]["content"]
    assert call["agent"] == "brief"
    assert "Write about pgvector" in content
    assert "HNSW was 3x faster than IVF." in content
    assert LONG_A in content
    assert NOTE in content


async def test_prompt_omits_sections_with_no_input() -> None:
    llm = FakeLLM(parse_replies=[make_draft()])

    await BriefAgent(llm).run(make_state())

    content = llm.calls[0]["messages"][0]["content"]
    assert "Attached material" not in content
    assert "Writing samples" not in content
    assert "Experience notes" not in content


async def test_clarifying_questions_are_passed_through() -> None:
    questions = ["Who is the audience: beginners or DBAs?"]
    llm = FakeLLM(parse_replies=[make_draft(questions)])

    update = await BriefAgent(llm).run(make_state(raw_prompt="vectors"))

    assert update["clarifying_questions"] == questions


def test_pick_excerpts_skips_short_paragraphs_and_caps_at_three() -> None:
    assert pick_excerpts([SAMPLE]) == [LONG_A, LONG_B, LONG_C]


def test_pick_excerpts_with_no_samples() -> None:
    assert pick_excerpts([]) == []


def test_llm_output_schema_has_no_unbounded_lists() -> None:
    assert find_unbounded_arrays(BriefDraft.model_json_schema()) == []
