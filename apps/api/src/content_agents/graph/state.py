"""Shared pipeline state and the schemas agents exchange (Plan.md §5.3)."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, HttpUrl, model_validator

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class Span(BaseModel):
    """Character offsets into the article markdown, end-exclusive."""

    start: int = Field(ge=0)
    end: int

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        return self


class Attachment(BaseModel):
    kind: Literal["image", "pdf", "docx", "audio", "url", "youtube"]
    uri: str
    mime_type: str | None = None


class ContentBrief(BaseModel):
    topic: str
    refined_prompt: str
    search_intent: Literal["informational", "commercial", "transactional", "navigational"]
    audience: str
    tone: str
    target_word_count: int = Field(gt=0, le=10_000)
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list, max_length=10)
    questions_to_answer: list[str] = Field(default_factory=list, max_length=10)
    locale: str = "en-US"
    format: Literal["blog", "listicle", "how-to", "comparison", "guide"]


class VoiceStyle(BaseModel):
    """The part of the voice the LLM infers from the request and writing samples."""

    persona: str
    pov: Literal["first_person", "second_person", "third_person"]
    formality: Literal["casual", "conversational", "professional"]
    signature_phrases: list[str] = Field(default_factory=list, max_length=8)
    avoid_phrases: list[str] = Field(default_factory=list, max_length=15)


class VoiceProfile(VoiceStyle):
    sample_excerpts: list[str] = Field(default_factory=list, max_length=3)
    experience_notes: list[str] = Field(default_factory=list, max_length=10)


class Source(BaseModel):
    url: HttpUrl
    title: str
    published_at: date | None = None
    domain: str
    credibility: Probability
    is_competitor: bool
    # Position in the primary-keyword results; set only for competitors.
    serp_rank: int | None = None
    word_count: int = 0
    markdown: str


class Citation(BaseModel):
    source_url: HttpUrl
    cited_text: str


class Fact(BaseModel):
    kind: Literal["statistic", "definition", "quote", "date", "claim"]
    text: str
    citation: Citation


class SerpBlueprint(BaseModel):
    must_cover: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    competitor_urls: list[HttpUrl] = Field(default_factory=list)
    median_word_count: int | None = None


class OutlineSection(BaseModel):
    heading: str
    level: Literal[2, 3]
    fact_indexes: list[int] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class Outline(BaseModel):
    title: str
    angle: str
    sections: list[OutlineSection]


class Draft(BaseModel):
    id: str
    angle: str
    markdown: str
    citations: list[Citation] = Field(default_factory=list)


class StyleMetrics(BaseModel):
    sentence_len_mean: float
    sentence_len_stdev: float
    paragraph_len_stdev: float
    mtld: float
    perplexity: float
    ai_phrase_hits: int = Field(ge=0)
    em_dash_per_1k: float
    transition_density: float
    repeated_openers: int = Field(ge=0)


class FlaggedSpan(BaseModel):
    span: Span
    ai_probability: Probability


class DetectorResult(BaseModel):
    detector: str
    ai_probability: Probability
    flagged_spans: list[FlaggedSpan] = Field(default_factory=list)


class Patch(BaseModel):
    span: Span
    original: str = Field(min_length=1)
    replacement: str
    reason: str


class AgentReport(BaseModel):
    agent: str
    score: float | None = None
    notes: list[str] = Field(default_factory=list)


class Scorecard(BaseModel):
    human_likeness: float
    coverage: float
    depth: float
    accuracy: float
    readability: float
    seo: float
    geo: float
    originality: float
    freshness: float
    weighted_total: float
    gaps: list[str] = Field(default_factory=list)


class FinalArticle(BaseModel):
    markdown: str
    html: str
    meta_title: str
    meta_description: str
    slug: str
    json_ld: list[dict[str, object]] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    scorecard: Scorecard
    detector_results: list[DetectorResult] = Field(default_factory=list)


class PipelineState(BaseModel):
    job_id: str
    raw_prompt: str
    attachments: list[Attachment] = Field(default_factory=list)
    # User-supplied voice inputs: the strongest human signals (Plan.md §6, Layer A).
    writing_samples: list[str] = Field(default_factory=list, max_length=5)
    experience_notes: list[str] = Field(default_factory=list, max_length=10)
    ingested_context: str | None = None
    seed_urls: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    brief: ContentBrief | None = None
    voice: VoiceProfile | None = None
    sources: list[Source] = Field(default_factory=list)
    people_also_ask: list[str] = Field(default_factory=list)
    fact_sheet: list[Fact] = Field(default_factory=list)
    serp_blueprint: SerpBlueprint | None = None
    outline: Outline | None = None
    article: Draft | None = None
    protected_spans: list[Span] = Field(default_factory=list)
    style_metrics: StyleMetrics | None = None
    detector_results: list[DetectorResult] = Field(default_factory=list)
    reports: dict[str, AgentReport] = Field(default_factory=dict)
    benchmark: Scorecard | None = None
    competitor_scores: list[Scorecard] = Field(default_factory=list)
    humanize_round: int = 0
    paid_detector_checks: int = 0
    revision: int = 0
    cost_usd: float = 0.0
    final: FinalArticle | None = None
