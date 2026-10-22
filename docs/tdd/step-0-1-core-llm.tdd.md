# TDD Evidence: Plan Steps 0–1 (scaffolding, core contracts, LLM client, cost tracking)

**Source plan:** [Plan.md](../../Plan.md), §11 Step 0 and Step 1
**Date:** 2026-10-22
**Runner:** pytest via uv (`apps/api`). The project is not a git repository yet, so there are no checkpoint commits; this report is the record.

## User journeys

1. **Agent settings in one table:** as the pipeline, each agent's model, effort and thinking mode come from one table, so cost tiering is config, not code.
2. **Cost and budget:** as the operator, every call's cost is computed from API usage and charged to a per-job budget, so no job can keep spending once its cap is reached.
3. **One client for every agent:** as an agent author, I get validated Pydantic output from one client that handles refusals and truncation, so agents don't deal with API edge cases.
4. **Validated shared state:** as the pipeline, shared state validates its data (spans, probabilities, briefs), so bad data fails fast.

## RED / GREEN record

| Stage | Command | Result |
|---|---|---|
| RED | `uv run pytest -q` | 4 collection errors: `ModuleNotFoundError: No module named 'content_agents.llm'`. A compile-time RED caused by the missing implementation. |
| GREEN (attempt 1) | `uv run pytest -q` | 54 passed, 1 failed: `test_max_tokens_stop_raises_truncated`. The SDK's `parse()` validated truncated JSON *before* the stop-reason check, raising a pydantic `ValidationError` and leaving the call's cost uncharged. |
| Fix | `client.parse()` now sends the schema via `beta.messages.create` (`output_config.format` + `anthropic.transform_schema`) and validates *after* the refusal and `max_tokens` checks. | — |
| GREEN | `uv run pytest -q` | 55 passed |
| Refactor | ruff (PEP 695 generic, line length), ruff format, removed the `uv init` placeholder | 55 passed |
| Coverage | `uv run pytest --cov` | 100% statements and branches (`fail_under = 80`) |
| Static | `uv run ruff check .`, `uv run mypy src tests` (strict) | clean |

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | Every agent in Plan §4.2 has a config; bulk agents use Haiku, the writer uses Sonnet | `test_tiers.py::test_every_plan_agent_has_a_config`, `test_bulk_agents_use_haiku`, `test_writer_uses_sonnet_by_default` | unit | PASS |
| 2 | Premium mode moves only Sonnet-tier agents to Opus | `test_tiers.py::test_premium_*` | unit | PASS |
| 3 | Thinking-off maps to `disabled` (Haiku) or `between_tools` (Sonnet), stays adaptive on Opus, and is rejected above `high` effort | `test_tiers.py::test_*thinking*` | unit | PASS |
| 4 | Prices: Sonnet, Opus, cache reads, 5-minute and 1-hour cache writes, Haiku ≤100K and >100K prompt tiers (cached tokens included) | `test_cost.py::test_*price*`, `test_cache_write_*`, `test_haiku_*` | unit | PASS |
| 5 | SDK usage with missing or `None` fields maps to zeros; 1-hour cache writes are read from the breakdown | `test_cost.py::test_usage_from_api_*` | unit | PASS |
| 6 | The budget accumulates spend, blocks calls once used up, and rejects a non-positive cap | `test_cost.py::test_budget_*` | unit | PASS |
| 7 | `parse()` returns a validated model and charges the budget | `test_client.py::test_parse_returns_validated_output_and_charges_budget` | integration (HTTP mock) | PASS |
| 8 | Sonnet requests carry the tier effort, adaptive thinking, automatic caching, `fallbacks: "default"` and the fallback beta header | `test_client.py::test_sonnet_request_uses_tier_effort_caching_and_server_fallback` | integration | PASS |
| 9 | Haiku requests disable thinking and send no server fallback | `test_client.py::test_haiku_request_disables_thinking_and_has_no_server_fallback` | integration | PASS |
| 10 | A Haiku refusal is retried once on Sonnet; both attempts are charged and reported in `cost_usd` | `test_client.py::test_haiku_refusal_retries_once_on_sonnet` | integration | PASS |
| 11 | Refusals that can't be retried raise `LLMRefusalError` with the category | `test_client.py::test_haiku_refusal_then_sonnet_refusal_raises`, `test_refusal_after_server_fallback_raises_without_client_retry` | integration | PASS |
| 12 | Truncated output raises `LLMTruncatedError` (after charging) | `test_client.py::test_max_tokens_stop_raises_truncated` | integration | PASS |
| 13 | An exhausted budget blocks the call before any HTTP request | `test_client.py::test_exhausted_budget_blocks_call_before_any_request` | integration | PASS |
| 14 | A 429 is retried by the SDK and then succeeds | `test_client.py::test_rate_limit_is_retried_then_succeeds` | integration | PASS |
| 15 | Fallback-served responses are priced at the serving model; unknown models are priced at the requested model | `test_client.py::test_response_served_by_fallback_model_*`, `test_response_from_unpriced_model_*` | integration | PASS |
| 16 | `text()` joins text blocks and skips thinking blocks; premium mode routes the writer to Opus | `test_client.py::test_text_*`, `test_premium_routes_writer_to_opus` | integration | PASS |
| 17 | State schemas reject invalid spans, out-of-range probabilities, empty patches, a non-positive word count and an unknown intent; list defaults aren't shared | `test_state.py::*` | unit | PASS |

The integration tests drive the real `anthropic` SDK through `httpx2.MockTransport`, so request bodies and headers are checked as sent on the wire. No live API calls were made.

## Revision: switched to local Ollama (same day)

The user chose local Ollama instead of the Claude API. The Anthropic SDK, Claude tiers, dollar pricing, refusal fallbacks and `test_cost.py` were removed. The new modules are `config.py` (`OllamaSettings`), `llm/budget.py` (token budget), a rewritten `llm/tiers.py` (bulk/writing tier, think, temperature) and a rewritten `llm/client.py`. **Rows 1–16 of the table above describe the removed Claude client and no longer apply.**

| Stage | Command | Result |
|---|---|---|
| RED | `uv run pytest -q` | 3 collection errors: `No module named 'content_agents.config'` and `content_agents.llm.budget` |
| GREEN | `uv run pytest -q` | 45 passed |
| Live smoke 1 | scratchpad script against Ollama 0.31.2, `qwen3-vl:8b` | **FAILED:** `LLMTruncatedError`. The model ignored `think=false` (answer in `thinking`, empty `content`), and with the instruct model an unbounded `keywords` list ran to `num_predict` |
| Fix | default to `qwen3-vl:8b-instruct`; global `OLLAMA_THINK` switch (`think` omitted when off); bound model-filled lists with `max_length` | 46 passed |
| Live smoke 2 | same script | PASS: parse in 5.5 s (including model load, 30 prompt / 60 output tokens), text in 2.2 s |
| Static / coverage | ruff, mypy strict, `pytest --cov` | clean, 100% |

| # | What is guaranteed (Ollama client) | Test | Result |
|---|---|---|---|
| O1 | `parse()` returns the validated model; prompt and output tokens are charged | `test_client.py::test_parse_returns_validated_output_and_charges_tokens` | PASS |
| O2 | Request shape: tier model, `stream:false`, schema in `format`, fixed `num_ctx`, `num_predict` = max_tokens, agent temperature, `keep_alive`, system message first | `test_bulk_request_shape` | PASS |
| O3 | The writer uses the writing model; `think` is sent only when the switch is on | `test_writer_uses_writing_model_with_thinking_and_no_format`, `test_thinking_switched_off_omits_think_field` | PASS |
| O4 | `text()` returns only the answer, never the thinking | `test_text_returns_content_without_thinking` | PASS |
| O5 | `done_reason == "length"` raises `LLMTruncatedError` after charging | `test_length_stop_raises_truncated_after_charging` | PASS |
| O6 | An exhausted budget blocks the call before any request | `test_exhausted_budget_blocks_call_before_any_request` | PASS |
| O7 | A down server or missing model raises `LLMUnavailableError` with the host or an `ollama pull` hint; other errors propagate | `test_unreachable_*`, `test_missing_model_*`, `test_other_server_errors_propagate` | PASS |
| O8 | Settings defaults, env overrides, `num_ctx > 0` | `test_settings.py::*` | PASS |
| O9 | Token budget accounting | `test_budget.py::*` | PASS |

## Known gaps and deviations

- **Not live-verified:** the request shapes match the bundled SDK reference and the installed SDK signatures, but no call has been made against the real API (no key used). Do a smoke test with a real `ANTHROPIC_API_KEY` before Step 2 depends on it.
- **Haiku 5.5 cache-read price** ($0.01/MTok) assumes the standard 0.1× of input; it isn't published in the reference used.
- **Haiku capability check** (Plan §9, via the Models API) is not implemented yet; it needs a live key.
- **Deferred from Step 1:** Langfuse tracing (to Step 12), `agents/base.py` (to Step 2, where the first agent exists), and a streaming `text` variant (to Step 6, where the Writer needs it).
- **Deferred from Step 0:** the Next.js app scaffold (to Step 11). `docker-compose.yml` was not run because Docker isn't installed on this machine.
- **Environment fix:** the editable-install `.pth` file was flagged hidden by macOS, which Python 3.13 skips. Tests use `pythonpath = ["src"]` so they don't depend on it.
