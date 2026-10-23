"""Agent 1: turn attachments into text context for the brief (Plan.md §7)."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import docx
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException

from content_agents.agents.base import ChatLLM, StateUpdate
from content_agents.graph.state import AgentReport, Attachment, PipelineState
from content_agents.llm.client import LLMTruncatedError

DEFAULT_MAX_CHARS_PER_ATTACHMENT = 12_000
IMAGE_MAX_TOKENS = 1_024
IMAGE_SYSTEM = (
    "You describe images for a writer researching an article. Report what the image "
    "shows: visible text, numbers, chart values and labels, and its main point. "
    "Be factual and do not speculate."
)
IMAGE_INSTRUCTION = "Describe this image for the article's research notes."

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


class IntakeError(Exception):
    """An attachment couldn't be turned into text."""


async def youtube_transcript(video_id: str) -> str:
    try:
        transcript = await asyncio.to_thread(YouTubeTranscriptApi().fetch, video_id)
    except YouTubeTranscriptApiException as e:
        raise IntakeError(str(e)) from e
    return " ".join(snippet.text for snippet in transcript)


async def whisper_transcribe(path: Path) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise IntakeError(
            "audio transcription needs faster-whisper: run `uv add faster-whisper`"
        ) from e

    def transcribe() -> str:
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(path))
        return " ".join(segment.text.strip() for segment in segments)

    return await asyncio.to_thread(transcribe)


def youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
                candidate = parts[1]
    if not _VIDEO_ID.fullmatch(candidate):
        raise IntakeError(f"not a YouTube video URL: {url}")
    return candidate


def _pdf_text(path: Path) -> str:
    try:
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PyPdfError as e:
        raise IntakeError(f"invalid PDF: {e}") from e
    if not text.strip():
        raise IntakeError("no extractable text (scanned PDF? OCR is not supported yet)")
    return text


def _docx_text(path: Path) -> str:
    try:
        document = docx.Document(str(path))
    except PackageNotFoundError as e:
        raise IntakeError(f"invalid DOCX: {e}") from e
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


class IntakeAgent:
    name = "intake"

    def __init__(
        self,
        llm: ChatLLM,
        *,
        fetch_youtube_transcript: Callable[[str], Awaitable[str]] = youtube_transcript,
        transcribe_audio: Callable[[Path], Awaitable[str]] = whisper_transcribe,
        max_chars_per_attachment: int = DEFAULT_MAX_CHARS_PER_ATTACHMENT,
    ) -> None:
        self._llm = llm
        self._fetch_youtube_transcript = fetch_youtube_transcript
        self._transcribe_audio = transcribe_audio
        self._max_chars = max_chars_per_attachment

    async def run(self, state: PipelineState) -> StateUpdate:
        sections: list[str] = []
        seed_urls: list[str] = []
        notes: list[str] = []
        for attachment in state.attachments:
            if attachment.kind == "url":
                # Fetched by the research agent alongside its own sources.
                seed_urls.append(attachment.uri)
                continue
            try:
                text = (await self._extract(attachment)).strip()
                if not text:
                    raise IntakeError("no extractable text")
            except (IntakeError, OSError, LLMTruncatedError) as e:
                # One bad attachment shouldn't sink the job; the note reaches the user.
                notes.append(f"could not read {attachment.kind} {attachment.uri}: {e}")
                continue
            sections.append(f"## {attachment.kind}: {_label(attachment)}\n{self._cap(text)}")

        return {
            "ingested_context": "\n\n".join(sections) or None,
            "seed_urls": seed_urls,
            "reports": {**state.reports, self.name: AgentReport(agent=self.name, notes=notes)},
        }

    async def _extract(self, attachment: Attachment) -> str:
        path = Path(attachment.uri)
        match attachment.kind:
            case "pdf":
                return await asyncio.to_thread(_pdf_text, path)
            case "docx":
                return await asyncio.to_thread(_docx_text, path)
            case "image":
                return await self._describe_image(path)
            case "youtube":
                return await self._fetch_youtube_transcript(youtube_video_id(attachment.uri))
            case "audio":
                return await self._transcribe_audio(path)
            case _:
                raise IntakeError(f"unsupported attachment kind: {attachment.kind}")

    async def _describe_image(self, path: Path) -> str:
        image = await asyncio.to_thread(path.read_bytes)
        result = await self._llm.text(
            self.name,
            system=IMAGE_SYSTEM,
            messages=[{"role": "user", "content": IMAGE_INSTRUCTION, "images": [image]}],
            max_tokens=IMAGE_MAX_TOKENS,
        )
        return result.output

    def _cap(self, text: str) -> str:
        overflow = len(text) - self._max_chars
        if overflow <= 0:
            return text
        return f"{text[: self._max_chars]}\n[truncated: {overflow} more characters]"


def _label(attachment: Attachment) -> str:
    return attachment.uri if attachment.kind == "youtube" else Path(attachment.uri).name
