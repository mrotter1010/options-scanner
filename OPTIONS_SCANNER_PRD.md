# Options Opportunity Scanner — Project Reference Document

**Project:** `options-scanner`
**Owner:** Mike Rotter
**Status:** Phase 1 in progress — Milestones 1.1–1.2 complete
**Last Updated:** 2026-07-11

---

## 1. Problem Statement

Identifying quality put-buying opportunities requires synthesizing multiple data streams simultaneously: earnings catalysts, news flow, implied volatility environment, options market structure, and technical context. Doing this manually across a broad universe of tickers is time-intensive and inconsistent. The goal is a system that does this scanning autonomously and surfaces only the most compelling bearish setups for human review and execution.

---

## 2. Objectives

**Primary objective:** Build an autonomous pipeline that identifies high-quality put-buying opportunities on a daily basis, requiring minimal manual effort beyond reviewing the ranked output and placing trades.

**Secondary objectives:**
- Keep the system free or near-free to operate at the data layer (beyond Schwab API, which is already in use)
- Build toward a trade log that feeds back into scoring refinement over time
- Keep the architecture modular so paid data sources (e.g. ORATS for IV rank, Unusual Whales for flow) can be swapped in later without rearchitecting

**Non-goals (for now):**
- Automated order execution (will review output manually and place trades through Schwab UI or manually via API)
- Call-side or spread strategies — starting with long puts only (defined risk, no margin)
- Real-time intraday scanning — daily premarket run is sufficient to start

---

## 3. Strategy & Approach

### 3.1 Target Trade Profile

The scanner is optimizing for a specific setup:

- **Direction:** Bearish (put buyers)
- **Risk profile:** Defined risk — long puts only, max loss = premium paid
- **Timeframe:** Short-term, 0–21 DTE preferred for catalysts; up to 45 DTE acceptable for thesis-driven plays
- **Entry condition:** IV rank below ~40 (buying cheap vol, not paying up for already-elevated options)
- **Catalyst requirement:** Prefer identifiable near-term catalyst (earnings, sector news, macro event, technical breakdown)
- **Liquidity floor:** Meaningful open interest and tight bid/ask spreads to enable clean entry/exit

### 3.2 Scoring Philosophy

Claude is the reasoning layer, not a prediction engine. It receives structured data and evaluates:

1. **Bearish thesis quality** — is there a coherent, defensible reason to expect downside?
2. **Catalyst credibility and timing** — is the catalyst real, near-term, and directionally clear?
3. **IV environment** — are options cheap relative to the ticker's own history?
4. **Risk/reward** — given current premium and likely move, does the trade have positive expected value?
5. **Invalidation conditions** — what price action or news would indicate the thesis is wrong?

Claude does not predict prices. It stress-tests theses and structures trades.

---

## 4. Data Architecture

### 4.1 Data Sources

| Data Need | Source | Cost | Notes |
|---|---|---|---|
| Options chains + Greeks | Schwab API | Free | Live data; core options layer |
| Price history + quotes | Schwab API | Free | |
| Index movers | Schwab API | Free | Good for sector rotation signals |
| Earnings calendar | Finnhub (free tier) | Free | 60 calls/min; endpoint: `/api/v1/calendar/earnings` |
| Company news by ticker | Finnhub (free tier) | Free | Endpoint: `/api/v1/company-news` |
| Macro/sector news | RSS (CNBC, Reuters, Nasdaq, MarketWatch) | Free | Via `FinNews` Python package or direct RSS parsing |
| Unusual options activity | Self-built on Schwab chain | Free | Vol/OI ratio > 3.0, premium > $25K filter |
| IV rank | Self-computed from Schwab IV → SQLite | Free | 3–6 month bootstrap period before useful |
| Put/call skew | Derived from Schwab chain | Free | OTM put IV vs OTM call IV at same delta |

**Known gaps (free tier limitations):**
- **IV rank** will be unavailable in a meaningful form for the first ~3–6 months while history accumulates. Use raw IV during this period and filter on IV rank once history is sufficient. ORATS is the paid alternative (~verify current pricing at orats.com) if this becomes a blocker.
- **Institutional flow / dark pool** data is not available free. Unusual Whales requires a paid API token. The vol/OI ratio filter is a reasonable approximation for self-built detection.
- **News sentiment scoring** is not automatically available free. Claude will perform qualitative sentiment assessment as part of the scoring prompt rather than relying on a pre-scored feed.

### 4.2 Schwab API Notes

- Two API product groups needed: `Market Data Production` and `Accounts and Trading Production`
- Authentication: OAuth 2.0. Access token expires every 30 minutes; refresh token has a **hard 7-day expiration** that cannot be extended — plan for a scheduled re-auth mechanism
- Rate limit: 120 requests/minute (verified from community docs — check developer.schwab.com for current limits)
- Options chains endpoint returns Greeks (delta, theta, vega, gamma) — no third-party needed for these
- Historical options pricing data is **not available** through the Schwab API

### 4.3 Storage

- SQLite for all persistent state (consistent with Vantyra patterns)
  - `iv_history` table: daily IV snapshots per ticker for IV rank computation
  - `earnings_calendar` table: forward-looking earnings dates, refreshed daily
  - `scan_results` table: daily scanner output with Claude scores
  - `trade_log` table: actual trades taken, linked back to scan results for feedback loop

---

## 5. System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   INGESTION LAYER                   │
│  Runs premarket (e.g. 8:00–9:00 AM ET via cron)   │
│                                                     │
│  Schwab API              External Sources           │
│  • Options chains + Greeks  • Finnhub earnings cal │
│  • Live quotes              • Finnhub company news  │
│  • Price history            • RSS news feeds        │
│  • Index movers                                     │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│                  SCREENING LAYER                    │
│  Rule-based filters — runs before Claude sees any  │
│  candidate (keeps Claude API costs bounded)         │
│                                                     │
│  • Has earnings or identifiable catalyst ≤ 21 days │
│  • IV rank < 40 (or raw IV below 6-month median    │
│    during bootstrap period)                         │
│  • Minimum options liquidity: OI > [TBD], bid/ask  │
│    spread < [TBD]% of mid                           │
│  • Unusual activity flag: vol/OI > 3.0 on puts,   │
│    premium > $25K                                   │
│  • Price > $10 (avoid penny stock noise)            │
│  • Min avg daily volume > [TBD]                     │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│               CLAUDE ANALYSIS LAYER                 │
│                                                     │
│  Per screened candidate, Claude receives:           │
│  • IV rank / raw IV + 6-month context               │
│  • Upcoming catalyst (type, date, expected move)    │
│  • Recent company news (past 7 days)               │
│  • Price action summary (vs 20/50 day MA, % from   │
│    52w high, recent trend)                          │
│  • Options chain snapshot (ATM IV, skew, OI dist)  │
│  • Unusual activity flag if triggered               │
│                                                     │
│  Claude outputs structured JSON:                    │
│  • Composite score (0–100)                          │
│  • Sub-scores: thesis quality, catalyst, IV env,   │
│    risk/reward, liquidity                           │
│  • Suggested structure: strike, expiration, rationale│
│  • Invalidation conditions                         │
│  • Short thesis summary (3–5 sentences)            │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│                   OUTPUT LAYER                      │
│                                                     │
│  Daily digest (CLI and/or simple HTML report):     │
│  • Top N candidates ranked by composite score      │
│  • Key metrics + Claude thesis summary per ticker  │
│  • Suggested trade structure                       │
│  • Invalidation triggers                           │
│  • Timestamp + data freshness indicators           │
└─────────────────────────────────────────────────────┘
```

---

## 6. Milestones

### Phase 1 — Thin but Functional (Target: ~2–3 weeks)

Goal: a working daily scanner that produces a ranked watchlist without any automated execution.

| Milestone | Description |
|---|---|
| 1.1 | Schwab API auth module with token refresh and 7-day re-auth handler |
| 1.2 | Options chain puller: given a ticker list, fetch chains + Greeks from Schwab |
| 1.3 | Finnhub earnings calendar ingestion: daily pull of upcoming earnings (next 21 days), stored to SQLite |
| 1.4 | IV snapshot recorder: daily job to record ATM IV per ticker into `iv_history` table |
| 1.5 | Unusual activity detector: vol/OI > 3.0, premium > $25K filter applied to Schwab chains |
| 1.6 | Claude scoring prompt v1: structured prompt + JSON output schema, tested manually on 5–10 candidates |
| 1.7 | CLI output: ranked watchlist printed to terminal with scores, thesis summaries, suggested structures |
| 1.8 | End-to-end test: run full pipeline on a real trading day, manually validate outputs against known setups |

**Phase 1 success criteria:** Scanner runs without intervention premarket and produces ≥5 candidates with coherent, actionable theses on a day with normal earnings flow.

---

### Phase 2 — Autonomous & Expanded (Target: ~4–6 weeks after Phase 1)

Goal: fully automated daily run, broader universe, news ingestion, improved output.

| Milestone | Description |
|---|---|
| 2.1 | Cron job setup: automated premarket run (8:00–9:00 AM ET), logs to file |
| 2.2 | RSS news ingestion: pull and parse CNBC, Reuters, Nasdaq feeds; filter by ticker mention |
| 2.3 | Sector/macro signal layer: identify broad bearish sector themes from news to weight individual scans |
| 2.4 | Expanded ticker universe: move beyond manually seeded list; use Schwab movers + Finnhub high-volume tickers as source pool |
| 2.5 | IV rank activation: phase in IV rank filter once `iv_history` has 60+ days of data per ticker |
| 2.6 | Output upgrade: HTML daily digest with sortable table, emailed or written to a local file |
| 2.7 | Trade log schema + UI: simple way to record trades taken, linked to scan output record |

**Phase 2 success criteria:** Pipeline runs fully autonomously for 2 consecutive weeks without intervention. Trade log has at least 10 entries. IV rank is computing for at least the top 50 most-scanned tickers.

---

### Phase 3 — Feedback Loop & Refinement (Target: ongoing after Phase 2)

Goal: use trade log data to refine scoring and identify what the system is getting right/wrong.

| Milestone | Description |
|---|---|
| 3.1 | Post-trade analysis: Claude reviews closed trades, compares thesis to actual outcome |
| 3.2 | Score calibration: identify which sub-scores (thesis quality, IV env, catalyst type, etc.) are most predictive of profitable trades |
| 3.3 | Screening threshold tuning: adjust liquidity floors, IV rank cutoffs, vol/OI thresholds based on observed results |
| 3.4 | Paid data evaluation: assess whether ORATS IV rank or Unusual Whales flow data would materially improve signal quality given observed gaps |
| 3.5 | Optional — Schwab order placement: for high-confidence plays (score > TBD threshold), explore programmatic order submission with position size limits |

---

## 7. Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.x |
| Broker API | Schwab Trader API (`schwabdev` — github.com/tylerebowers/Schwabdev) |
| Earnings + news | Finnhub REST API (free tier) |
| News feeds | `FinNews` package or direct `feedparser` RSS parsing |
| Storage | SQLite (via `sqlite3` stdlib or `sqlalchemy`) |
| Scheduling | `cron` (macOS/Linux) |
| LLM reasoning | Anthropic API — Claude Sonnet (cost-efficient for daily batch) |
| Output | CLI + optional HTML digest |

---

## 8. Cost Model

| Item | Cost |
|---|---|
| Schwab API | $0 |
| Finnhub free tier | $0 |
| RSS feeds | $0 |
| Anthropic API (Claude Sonnet) | ~$0.003 per scored candidate; 20 candidates/day ≈ $0.06/day ≈ ~$1.80/month |
| ORATS (optional, Phase 3 evaluation) | Verify current pricing at orats.com |
| Unusual Whales API (optional, Phase 3) | Verify current pricing at unusualwhales.com |

**Total Phase 1–2 operating cost: effectively $0 + Claude API usage (~$2/month at 20 candidates/day)**

---

## 9. Open Questions / Decisions Deferred

- **Ticker universe seeding:** What's the initial pool of tickers to scan? Options: S&P 500 universe, Schwab index movers, Finnhub high-volume filter, or manually maintained watchlist. Start with a narrow seeded list (~50–100 tickers) and expand in Phase 2.
- **Liquidity thresholds:** Specific OI and bid/ask spread floors need to be set empirically based on the kinds of trades being targeted. Placeholder — calibrate during Phase 1 testing.
- **Claude model selection:** Sonnet is the cost-efficient default. Opus is worth evaluating if scoring quality on ambiguous theses proves insufficient.
- **Re-auth for Schwab 7-day token expiry:** Resolved. `schwabdev` handles 30-minute access token refresh automatically. The 7-day refresh token hard expiry is unavoidable — when it lapses, the auth module will detect it and prompt for a manual browser-based re-authorization. No headless workaround exists; this is enforced server-side by Schwab.
- **Position sizing:** Not in scope for the scanner itself, but needs to be defined before any Phase 3 automated execution work begins.

---

## 10. Reference Notes

- This project is separate from Vantyra Analytics infrastructure but uses the same development workflow (Claude for architectural planning, Claude Code for implementation, Git Flow)
- **Repo:** github.com/mrotter1010/options-scanner — local path: `/Users/michaelrotter/options-scanner`
- Schwab API developer portal: developer.schwab.com
- Finnhub docs: finnhub.io/docs/api
- `schwabdev` wrapper (sole Schwab library — handles auth + data): github.com/tylerebowers/Schwabdev
- `FinNews` package: github.com/scaratozzolo/FinNews
- Key architectural decision: `schwabdev` selected over `schwab-py` for its more active maintenance cadence (v3.0.5, May 2026, Production/Stable) and built-in token encryption. Mixing the two libraries was evaluated and ruled out — both manage OAuth session state internally and would create token refresh race conditions if used together.
- Key architectural decision: build unusual activity detector on top of Schwab chains rather than relying on Unusual Whales (saves ~$30–50/month and keeps the free-tier stack intact through Phase 2)

---

## 11. Development Workflow & Conventions

### 11.1 Roles

| Role | Who | Responsibilities |
|---|---|---|
| Project Director | Mike | Vision, decisions, priorities, unblocking |
| Project Manager | Claude (planning chat) | Architecture, design, milestone planning, prompt drafting, PRD maintenance |
| Engineer | Claude Code | Implementation only — no independent investigation or research |

### 11.2 Milestone Workflow

Each milestone follows this sequence:

1. **Milestone chat opened** with a brief (see template in Section 12) covering goals, project status, and what has been completed to date
2. **Architecture and design questions** resolved in the milestone chat before any code is written
3. **Claude Code prompts drafted** in the milestone chat — implementation-ready, no ambiguity left for Claude Code to resolve independently
4. **Implementation** executed by Claude Code on the milestone's feature branch
5. **At close of milestone chat:**
   - PRD Progress Log (Section 13) updated to reflect what was completed
   - Brief for the *next* milestone drafted while context is fresh

### 11.3 Claude Code Guidelines

Claude Code is an implementation tool, not a research or decision-making tool. The following rules apply to all Claude Code prompts and sessions:

- **Implementation only.** Claude Code executes against a defined spec. It does not investigate alternatives, evaluate libraries, or make architectural decisions independently.
- **No rabbit holes.** If Claude Code encounters an unexpected issue or ambiguity, it stops and asks for clarification — it does not attempt to resolve it autonomously.
- **No independent research.** Claude Code does not search the web, explore documentation speculatively, or investigate approaches not specified in the prompt.
- **Pause and surface blockers.** If something in the spec is unclear or a dependency behaves unexpectedly, Claude Code flags it explicitly and waits for direction.

### 11.4 Git & Branch Conventions

- **No development on `main`.** All work happens on feature branches.
- **Branch naming:** `milestone/1.1-schwab-auth`, `milestone/1.2-chain-puller`, etc.
- **One branch per milestone.**
- **Squash merge to `main`** at the end of each milestone once tests pass. One clean commit per milestone on main; full branch history preserved on the feature branch for reference.
- Commit messages should be descriptive and reference the milestone number (e.g. `[1.1] Add token refresh handler`).

### 11.5 Testing Standards

- **Framework:** pytest
- **Coverage threshold:** 95% minimum. Any file below threshold must have a documented reason.
- **Failing tests must have explanations** — a red test with no comment is not acceptable.
- **Two test layers:**
  - **Unit/mock tests** — fast, offline, no real API calls. Mocks must be constructed from actual live API responses, not invented fixtures. Document the source response for each mock.
  - **Live integration tests** — real API calls, run deliberately (not in CI by default). Verify the API is reachable and returning the expected response shape. Kept in a separate `/tests/live/` directory. Marked with `@pytest.mark.live` and excluded from default runs via `addopts = "-m 'not live'"` in `pyproject.toml` (added at Milestone 1.2 close; applies to all live tests project-wide, including 1.1's).
- **Test naming:** `test_<module>_<behavior>` — tests should read as specifications, not as code descriptions.
- **No invented fixtures.** If a mock requires a specific API response shape, capture a real response first, then build the mock from it.

---

## 12. Milestone Brief Template

Each milestone chat is opened with a brief using this structure. The brief is drafted at the close of the *previous* milestone chat.

```
# Milestone [X.Y] Brief — [Short Title]

## Project Status
[1–2 sentences on overall project state: what phase, what's working, what's pending]

## Completed to Date
[Bulleted list of milestones completed, with a one-line note on anything non-obvious
about the implementation that's relevant going forward]

## This Milestone
**Goal:** [One sentence — what does "done" look like?]

**Scope:**
- [Specific deliverable 1]
- [Specific deliverable 2]
- [...]

**Out of Scope:**
- [Anything adjacent that might seem relevant but isn't this milestone's problem]

## Inputs / Dependencies
[What does this milestone depend on? Completed modules, env vars, config, external
services that need to be live, decisions that need to be made before implementation starts]

## Known Constraints or Risks
[Anything from prior milestones that affects this one — API quirks, deferred decisions
that now need resolution, known edge cases]

## Success Criteria
[How do we know this milestone is done? Should be verifiable, not subjective]
```

---

## 13. Progress Log

*Updated at the close of each milestone chat.*

| Milestone | Status | Completed | Notes |
|---|---|---|---|
| 1.1 — Schwab auth module | ✅ Complete | 2026-07-08 | `get_client()` returns authenticated `schwabdev.Client`. Tokens stored at `data/tokens.json`. Refresh token expiry detection at 48h threshold. 98% test coverage (13 tests). Callback URL must be registered without trailing slash in Schwab developer portal. `_refresh_token_issued` is a private attribute — monitor across `schwabdev` version upgrades. |
| 1.2 — Options chain puller | ✅ Complete | 2026-07-11 | `get_chain`/`get_chains` in `src/market_data/chains.py`. Puts-only, 45 DTE default fetch scope (configurable). `NoOptionsDataError` (per-ticker skip: empty chain or 400) vs `ChainFetchError` (systemic abort: unexpected status, repeated 429, or all-tickers-failed) distinction. 429 handling retries once after 60s backoff. 100% coverage, fixtures captured from real AAPL/VTSAX/ZZZZZ responses. `@pytest.mark.live` marker added retroactively to both 1.1 and 1.2 live tests, closing a gap where live tests were previously collected (though not run) by default `pytest tests/`. |
| 1.3 — Finnhub earnings calendar | Not started | — | — |
| 1.4 — IV snapshot recorder | Not started | — | — |
| 1.5 — Unusual activity detector | Not started | — | — |
| 1.6 — Claude scoring prompt v1 | Not started | — | — |
| 1.7 — CLI output | Not started | — | — |
| 1.8 — End-to-end test | Not started | — | — |
| 2.1 — Cron job setup | Not started | — | — |
| 2.2 — RSS news ingestion | Not started | — | — |
| 2.3 — Sector/macro signal layer | Not started | — | — |
| 2.4 — Expanded ticker universe | Not started | — | — |
| 2.5 — IV rank activation | Not started | — | — |
| 2.6 — Output upgrade | Not started | — | — |
| 2.7 — Trade log schema + UI | Not started | — | — |
| 3.1 — Post-trade analysis | Not started | — | — |
| 3.2 — Score calibration | Not started | — | — |
| 3.3 — Screening threshold tuning | Not started | — | — |
| 3.4 — Paid data evaluation | Not started | — | — |
| 3.5 — Schwab order placement (optional) | Not started | — | — |
