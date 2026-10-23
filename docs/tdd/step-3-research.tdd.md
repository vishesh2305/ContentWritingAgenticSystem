# TDD Evidence: Plan Step 3 (Research & Scraper with caches)

**Source plan:** [Plan.md](../../Plan.md), §11 Step 3, with the v3 local-Ollama note
**Dates:** 2026-10-22 to 2026-10-23
**Runner:** pytest via uv (`apps/api`). Not a git repository, so there are no checkpoint commits.

## Decisions taken during the step

| Plan said | Built | Why |
|---|---|---|
| Serper search | **DuckDuckGo (`ddgs`) by default**; Serper used automatically when `SERPER_API_KEY` is set | The user chose a local setup without API keys |
| Crawl4AI (Playwright) | **httpx + trafilatura** | Much lighter install and faster; Crawl4AI can be added later as a fallback for JavaScript-heavy pages |
| Postgres cache | **SQLite file cache** (`cache.py`) with the same get/set interface | Docker and Postgres aren't available on this machine; can move to Postgres in Step 4 |

## User journeys

1. **Real sources:** given a brief, the pipeline gets current, credible web sources, and the top results for the primary keyword are flagged as **competitors** for Step 5.
2. **Safe and polite scraping:** public addresses only (SSRF guard, re-checked on every redirect), `robots.txt` respected, one request at a time per host with a delay, and size and content-type limits.
3. **Clean sources:** duplicate URLs and near-duplicate pages removed, thin pages dropped, every source scored for credibility.
4. **Cheap re-runs:** search results cached 24 h, pages 7 days, query plans 24 h, permanent page failures 6 h.
5. **Resilient:** a failed search or page is reported and the job continues.

## RED / GREEN record

| Stage | Result |
|---|---|
| RED (compile) | 6 collection errors: `security`, `tools`, `cache` and `agents.research` missing |
| GREEN attempt 1 | 147 passed, 2 failed. **Real bug:** trafilatura returns navigation text ("Menu") for pages with no article, so a 20-word minimum was added. One test bug: a fake route was missing for the `?utm_source` URL. |
| GREEN | 149 passed |
| Live run 1 | 15 and 9 sources (≥8 ✓). Problems: DuckDuckGo throttled the parallel query burst; the planner wrote new queries on the repeat run (no cache benefit); blocked pages were retried each run |
| RED → GREEN (fixes) | DuckDuckGo serialised + spaced + one retry; query plan cached per brief; permanent failures remembered, transient ones not. 155 passed |
| Live run 2 | **Hung for over 10 minutes.** Diagnosed by walking the asyncio await chains: one `ddgs` call never returned (despite the library's own 5 s timeout) while holding the queue lock |
| RED → GREEN (fix) | `asyncio.wait_for` around every DuckDuckGo call; a stuck call becomes a reported failure and frees the queue. 156 passed |
| Live run 3 | No hang. Results below |
| Static / coverage | ruff, strict mypy clean; 97% total |

## Test specification (Step 3 additions)

| # | What is guaranteed | Tests |
|---|---|---|
| 1 | Only public http(s) URLs: rejects other schemes, embedded credentials, private/loopback/link-local/metadata/multicast IPs (v4 and v6), hosts resolving to any private address, and unresolvable hosts | `test_url_guard.py::*` |
| 2 | Canonical URLs: lowercase host, no `www.`, default port, fragment or trailing slash; tracking parameters stripped; remaining parameters sorted | `test_sources.py::test_canonical_url` |
| 3 | Near-duplicates (5-word shingles, Jaccard ≥ 0.8) are dropped, keeping the first copy | `test_sources.py::test_*duplicate*` |
| 4 | Credibility ranks institutional and reference sites up, low-quality sites down, and authored and recent pages up; old or thin pages down; range 0–1 | `test_sources.py::test_*credib*` |
| 5 | SQLite cache: round trip, TTL expiry, namespace isolation, overwrite, persistence across instances | `test_cache.py::*` |
| 6 | Serper request and response mapping (`organic`, `peopleAlsoAsk`); HTTP errors become `SearchError` | `test_search.py::test_serper_*` |
| 7 | DuckDuckGo: mapping, one retry, spacing between queries, timeout that frees the queue | `test_search.py::test_duckduckgo_*` |
| 8 | Search cache keyed on normalised query and result count; factory picks Serper only when a key is set | `test_search.py::test_cached_*`, `test_factory_*` |
| 9 | Fetcher: Markdown plus metadata extraction; robots disallow; robots fetched once per host; safe redirects followed; redirects to private hosts blocked; redirect loops stopped; HTTP errors, non-HTML, oversized and empty pages rejected; network errors reported; cache hit on canonical URL; per-host delay | `test_fetch.py::*` |
| 10 | Permanent failures remembered; 429/503 retried next run | `test_fetch.py::test_permanent_*`, `test_transient_*` |
| 11 | Research agent: needs a brief; planner gets the brief; competitors flagged with SERP rank; seed URLs fetched; one fetch per canonical page; failures noted; thin and near-duplicate pages dropped; cap keeps competitors plus the most credible others; "People also ask" questions collected; query plan reused for the same brief | `agents/test_research.py::*` |

## Live results (run 3, DuckDuckGo, real web, local Ollama)

| Topic | Sources | Competitors | Time | Notes |
|---|---|---|---|---|
| pgvector hnsw vs ivfflat | 9 | **0** | 82 s | DuckDuckGo returned "No results found" for the primary keyword and 2 planned queries (it returned results an hour earlier: run 1 found 8 competitors) |
| sourdough starter troubleshooting | 15 | 3 | 104 s | **Off-topic pages included**: car "starter motor" sites from an ambiguous planned query |
| pgvector (repeat) | 9 | 0 | 142 s | Pages and plan came from the cache, but the failed searches were retried and timed out |

## Known gaps (open)

1. **Search reliability:** free DuckDuckGo rate-limits this IP after repeated use. Options: a Serper key (already supported; free tier available), self-hosted SearXNG (needs Docker), or accepting slower, flakier runs. **This is the user's call.**
2. **Topic relevance:** no relevance filter yet. Plan: embedding similarity between the brief and each page, using Ollama `qwen3-embedding:0.6b`, at the start of Step 4.
3. **Competitor detection depends on search quality:** zero competitors means Step 5 has nothing to compare against.
4. **Performance:** `OLLAMA_NUM_CTX=32768` puts about 15% of the model on CPU on this 16 GB machine; 16384 likely fits fully in GPU memory.
5. **Bot blocks:** many sites return 403 to the crawler's honest user agent. Kept by design: the crawler identifies itself rather than posing as a browser.
6. **Abandoned threads:** a timed-out DuckDuckGo thread is abandoned, not killed, until it returns.
