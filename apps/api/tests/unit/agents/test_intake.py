import importlib.util
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import docx
import pytest
from fakes import FakeLLM
from fpdf import FPDF
from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException

from content_agents.agents import intake
from content_agents.agents.intake import IntakeAgent, IntakeError, youtube_video_id
from content_agents.graph.state import Attachment, PipelineState

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"


def make_pdf(path: Path, text: str) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 10, text)
    pdf.output(str(path))
    return path


def make_docx(path: Path, *paragraphs: str) -> Path:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))
    return path


def state_with(*attachments: Attachment) -> PipelineState:
    return PipelineState(
        job_id="job", raw_prompt="Write about pgvector", attachments=list(attachments)
    )


async def no_youtube(video_id: str) -> str:
    raise AssertionError("youtube fetch not expected")


async def no_audio(path: Path) -> str:
    raise AssertionError("audio transcription not expected")


def make_agent(
    llm: FakeLLM | None = None,
    *,
    fetch_youtube: Callable[[str], Awaitable[str]] = no_youtube,
    transcribe_audio: Callable[[Path], Awaitable[str]] = no_audio,
    max_chars: int = 12_000,
) -> IntakeAgent:
    return IntakeAgent(
        llm or FakeLLM(),
        fetch_youtube_transcript=fetch_youtube,
        transcribe_audio=transcribe_audio,
        max_chars_per_attachment=max_chars,
    )


async def test_prompt_without_attachments_has_no_context_and_no_llm_call() -> None:
    llm = FakeLLM()

    update = await make_agent(llm).run(state_with())

    assert update["ingested_context"] is None
    assert update["seed_urls"] == []
    assert llm.calls == []


async def test_pdf_text_is_extracted(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "report.pdf", "pgvector adds vector similarity search to Postgres.")

    update = await make_agent().run(state_with(Attachment(kind="pdf", uri=str(pdf))))

    assert "pdf: report.pdf" in update["ingested_context"]
    assert "vector similarity search" in update["ingested_context"]


async def test_docx_paragraphs_are_extracted(tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "notes.docx", "First point about HNSW.", "Second point about IVF.")

    update = await make_agent().run(state_with(Attachment(kind="docx", uri=str(doc))))

    assert "First point about HNSW." in update["ingested_context"]
    assert "Second point about IVF." in update["ingested_context"]


async def test_image_is_described_by_the_vision_model(tmp_path: Path) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(PNG_BYTES)
    llm = FakeLLM(text_replies=["A bar chart comparing query latency."])

    update = await make_agent(llm).run(state_with(Attachment(kind="image", uri=str(image))))

    assert llm.calls[0]["agent"] == "intake"
    assert llm.calls[0]["messages"][0]["images"] == [PNG_BYTES]
    assert "A bar chart comparing query latency." in update["ingested_context"]


async def test_youtube_transcript_is_fetched_by_video_id() -> None:
    requested: list[str] = []

    async def fetch(video_id: str) -> str:
        requested.append(video_id)
        return "In this talk we benchmark pgvector."

    agent = make_agent(fetch_youtube=fetch)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    update = await agent.run(state_with(Attachment(kind="youtube", uri=url)))

    assert requested == ["dQw4w9WgXcQ"]
    assert "In this talk we benchmark pgvector." in update["ingested_context"]


async def test_audio_is_transcribed(tmp_path: Path) -> None:
    audio = tmp_path / "memo.m4a"
    audio.write_bytes(b"fake-audio")
    transcribed: list[Path] = []

    async def transcribe(path: Path) -> str:
        transcribed.append(path)
        return "Remember to mention index build times."

    agent = make_agent(transcribe_audio=transcribe)
    update = await agent.run(state_with(Attachment(kind="audio", uri=str(audio))))

    assert transcribed == [audio]
    assert "Remember to mention index build times." in update["ingested_context"]


async def test_urls_become_seed_sources_without_fetching() -> None:
    url = "https://github.com/pgvector/pgvector"

    update = await make_agent().run(state_with(Attachment(kind="url", uri=url)))

    assert update["seed_urls"] == [url]
    assert update["ingested_context"] is None


async def test_long_attachment_is_truncated_with_a_visible_marker(tmp_path: Path) -> None:
    doc = make_docx(tmp_path / "long.docx", "x" * 500)

    update = await make_agent(max_chars=100).run(state_with(Attachment(kind="docx", uri=str(doc))))

    assert "x" * 100 in update["ingested_context"]
    assert "x" * 101 not in update["ingested_context"]
    assert "[truncated: 400 more characters]" in update["ingested_context"]


async def test_unreadable_attachment_is_reported_and_others_still_processed(
    tmp_path: Path,
) -> None:
    good = make_docx(tmp_path / "good.docx", "Readable content.")
    missing = tmp_path / "missing.pdf"

    update = await make_agent().run(
        state_with(
            Attachment(kind="pdf", uri=str(missing)),
            Attachment(kind="docx", uri=str(good)),
        )
    )

    assert "Readable content." in update["ingested_context"]
    notes = update["reports"]["intake"].notes
    assert len(notes) == 1
    assert "missing.pdf" in notes[0]


async def test_pdf_without_text_layer_is_reported(tmp_path: Path) -> None:
    blank = make_pdf(tmp_path / "scan.pdf", "")

    update = await make_agent().run(state_with(Attachment(kind="pdf", uri=str(blank))))

    assert update["ingested_context"] is None
    assert "no extractable text" in update["reports"]["intake"].notes[0]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_video_id_parsing(url: str) -> None:
    assert youtube_video_id(url) == "dQw4w9WgXcQ"


def test_non_video_youtube_url_raises() -> None:
    with pytest.raises(IntakeError):
        youtube_video_id("https://www.youtube.com/@somechannel")


async def test_default_youtube_fetcher_joins_snippets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(self: object, video_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(text="Indexes matter."), SimpleNamespace(text="So does recall.")]

    monkeypatch.setattr(YouTubeTranscriptApi, "fetch", fetch)

    assert await intake.youtube_transcript("dQw4w9WgXcQ") == "Indexes matter. So does recall."


async def test_default_youtube_fetcher_wraps_library_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def disabled(self: object, video_id: str) -> object:
        raise YouTubeTranscriptApiException("transcripts disabled")

    monkeypatch.setattr(YouTubeTranscriptApi, "fetch", disabled)

    with pytest.raises(IntakeError, match="transcripts disabled"):
        await intake.youtube_transcript("dQw4w9WgXcQ")


@pytest.mark.parametrize(
    ("kind", "filename", "message"),
    [
        ("pdf", "broken.pdf", "invalid PDF"),
        ("docx", "broken.docx", "invalid DOCX"),
    ],
)
async def test_corrupt_files_are_reported(
    tmp_path: Path, kind: str, filename: str, message: str
) -> None:
    corrupt = tmp_path / filename
    corrupt.write_bytes(b"this is not a real document")

    update = await make_agent().run(state_with(Attachment(kind=kind, uri=str(corrupt))))

    assert message in update["reports"]["intake"].notes[0]


async def test_empty_docx_is_reported(tmp_path: Path) -> None:
    empty = make_docx(tmp_path / "empty.docx")

    update = await make_agent().run(state_with(Attachment(kind="docx", uri=str(empty))))

    assert update["ingested_context"] is None
    assert "no extractable text" in update["reports"]["intake"].notes[0]


@pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is not None,
    reason="only meaningful when faster-whisper is not installed",
)
async def test_default_transcriber_explains_missing_dependency(tmp_path: Path) -> None:
    with pytest.raises(IntakeError, match="faster-whisper"):
        await intake.whisper_transcribe(tmp_path / "memo.m4a")
