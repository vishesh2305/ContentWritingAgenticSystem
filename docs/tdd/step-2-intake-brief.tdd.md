# TDD Evidence: Plan Step 2 (Intake & Multimodal Ingest, Brief + Voice Profile)

**Source plan:** [Plan.md](../../Plan.md), §11 Step 2, with the v3 local-Ollama note
**Date:** 2026-10-22
**Runner:** pytest via uv (`apps/api`). Not a git repository, so there are no checkpoint commits.

## User journeys

1. **Attachments:** a user attaches images, PDFs, DOCX files, YouTube links, audio or URLs, and each becomes text context for the brief.
2. **Bad attachments:** one unreadable attachment doesn't kill the job; it is reported and the rest continue.
3. **Brief and voice:** the pipeline gets a validated `ContentBrief` and `VoiceProfile` from the prompt, the context, the user's writing samples and experience notes.
4. **Clarification:** the user is asked questions only when the request is genuinely ambiguous.
5. **Runaway guard:** the LLM client refuses any output schema with an unbounded list (turning a Step 1 live finding into a hard rule).

## RED / GREEN record

| Stage | Command | Result |
|---|---|---|
| RED (compile) | `uv run pytest -q` | 3 collection errors: `No module named 'content_agents.llm.schema'`, plus `agents.intake` and `agents.brief` |
| RED (runtime) | `uv run pytest -q tests/unit/test_client.py` | `test_schema_with_unbounded_list_is_rejected_before_any_request` FAILED: the request was sent (`IndexError` in the fake server) |
| GREEN | `uv run pytest -q` | 76 passed |
| Refactor | strict mypy flagged that tests reached library names through `intake`; added tests for the corrupt PDF/DOCX, empty DOCX and YouTube success paths | 80 passed |
| Coverage | `uv run pytest --cov` | 97.98% total; `intake.py` 94% (uncovered: real faster-whisper transcription, unreachable `case _`) |
| Static | `ruff check .`, `mypy src tests` (strict) | clean |
| Live | scratchpad `step2_live.py` against Ollama 0.31.2, `qwen3-vl:8b-instruct` | 4/4 cases produced valid briefs, 18–27 s per case (see below) |

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | No attachments: no context, no LLM call | `test_intake.py::test_prompt_without_attachments_*` | unit | PASS |
| 2 | PDF and DOCX text is extracted and labelled by file name | `test_pdf_text_is_extracted`, `test_docx_paragraphs_are_extracted` | integration (real files) | PASS |
| 3 | Images are described by the vision model (bytes sent as `images`) | `test_image_is_described_by_the_vision_model` | unit | PASS |
| 4 | YouTube: video-ID parsing for watch, youtu.be, shorts and embed URLs; the transcript is fetched by ID; snippets are joined; library errors are wrapped | `test_youtube_*`, `test_default_youtube_fetcher_*` | unit | PASS |
| 5 | Audio is transcribed through the injected transcriber; without faster-whisper, the error says how to install it | `test_audio_is_transcribed`, `test_default_transcriber_explains_missing_dependency` | unit | PASS |
| 6 | URLs become seed sources for research and aren't fetched at intake | `test_urls_become_seed_sources_without_fetching` | unit | PASS |
| 7 | Long attachments are truncated with a visible `[truncated: N more characters]` marker | `test_long_attachment_is_truncated_*` | unit | PASS |
| 8 | Missing, corrupt, empty or scanned files are reported in `reports["intake"]` and the other attachments still process | `test_unreadable_*`, `test_corrupt_files_are_reported`, `test_empty_docx_is_reported`, `test_pdf_without_text_layer_*` | integration | PASS |
| 9 | Brief agent returns a brief and a voice profile; user excerpts and experience notes are attached to the voice | `test_brief.py::test_returns_brief_and_voice_profile` | unit | PASS |
| 10 | The prompt includes the request, attached material, writing samples and notes, and omits empty sections | `test_prompt_includes_*`, `test_prompt_omits_*` | unit | PASS |
| 11 | Clarifying questions pass through | `test_clarifying_questions_are_passed_through` | unit | PASS |
| 12 | Excerpts: paragraphs of 40–250 words, at most 3 | `test_pick_excerpts_*` | unit | PASS |
| 13 | No unbounded lists: the brief schema passes; the checker finds unbounded arrays at top level, in nested `$defs` and in list-of-list items; the client rejects such a schema before any request | `test_llm_output_schema_has_no_unbounded_lists`, `test_schema.py::*`, `test_client.py::test_schema_with_unbounded_list_*` | unit | PASS |

## Live results (not assertions; recorded for review)

| Case | Time | Outcome |
|---|---|---|
| vague: "vectors" | 26.5 s | Valid brief, but it **assumed math vectors** instead of asking a clarifying question |
| detailed: HNSW vs IVFFlat, 1500 words | 23.0 s | Good: comparison, 1500 words, keyword "HNSW vs IVFFlat pgvector" |
| image: benchmark chart | 27.3 s | Chart described correctly; brief built on it |
| PDF + writing sample + note | 18.3 s | The voice matched the sample's style; 1 excerpt and 1 note attached. Search intent was **wrongly** "transactional" |

## Known gaps

- **Plan check partly met:** the plan's "10 sample prompts" check was run on 4. The rhythm check (voice metrics within ±20%) needs the style-analysis module from Step 8.
- **Prompt tuning needed:** the 8B model rarely asks clarifying questions and sometimes mislabels search intent. Tune the prompt, or add a deterministic intent heuristic, before Step 3 relies on intent.
- **Not supported yet:** OCR for scanned PDFs, and audio without `uv add faster-whisper`, which is optional to keep installs light.
