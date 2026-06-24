# Improvement Plan — Geopolitical Risk Agent

_Derived from the 9-query quality evaluation (avg 6.1/10). This plan maps every
finding to a concrete root cause in the current code, a fix approach with named
files/functions, an implementation phase, a verification metric, and a complexity
estimate (S = <0.5 day, M = 0.5–2 days, L = >2 days)._

---

## Executive Summary

The agent is structurally sound — a 7-node LangGraph pipeline (Planner → RAG Research
→ Signals → Analysis → Consistency Validator → Reviewer → final_output) with deterministic
verdict enforcement, a retry loop, and a portfolio impact path. The evaluation shows the
problems are not architectural; they are **gaps in input hygiene, context scoping, and
synthesis depth**:

1. **Source hygiene is incomplete.** The news/web pipeline blocks crypto and disinfo
   domains but does not block social-media or video URLs (`facebook.com`, `youtube.com`),
   so the same Facebook post is injected as "LIVE NEWS" in ~50% of queries. The corpus
   surfaces documents under cryptic raw filenames (`ar2023e.pdf`, `sdnea2023001.pdf`) with
   no human-readable label.
2. **Context scoping is keyword-only.** `signals_node` uses literal country/region string
   matching, so it misses topical scope ("Global South" returns no countries; "EM bonds"
   returns only USA). The retriever has a fixed similarity floor that lets tangential
   corpus hits (`us_china_trade.txt` for a Brazil/Indonesia query) through.
3. **No ambiguity gate.** The Planner always decomposes into 4–6 sub-questions and never
   refuses; a hopelessly vague query ("invest in the Global South?") gets a confident
   generic answer.
4. **Synthesis is shallow on hard queries.** Scenario prices are not anchored to the live
   feed; there is no net-portfolio roll-up; the Analysis node conflates geographic
   proximity with supply-chain exposure; and the Consistency Validator only checks
   portfolio-verdict ↔ takeaway direction — it does not catch self-contradictory scenario
   text.

The plan below is sequenced so the cheapest, highest-impact fixes (source blocklists,
corpus relabeling, scenario anchoring) land first, followed by the scoping and synthesis
work, then the harder ambiguity/net-portfolio features.

---

## Priority Tiers

### CRITICAL

---

#### C1 — Persistent Facebook / social-media source injected as "LIVE NEWS"
**Evaluation refs:** E1, E2, H1, H3 (~50% of queries)
**Root cause:**
`src/georisk_agent/news/fetcher.py` maintains `_BLOCKED_DOMAINS`, but it only contains
disinfo and crypto outlets — **no social-media or UGC domains**. A Facebook post URL
therefore passes `_normalise_article()`. Worse, the news fetcher's blocklist is **not
applied to the Tavily web-fallback path at all**: `rag/web_search.py::search_web()` and
`agents/nodes_rag_research.py` (web-fallback block, lines ~105–139) append results with
zero domain filtering. The persisted Facebook chunk also re-enters via
`ephemeral_embeddings` (it is upserted by `ingest_web_results`), so it keeps resurfacing
through `retrieve_ephemeral()` long after the original fetch.

**Fix approach:**
1. In `news/fetcher.py`, extend `_BLOCKED_DOMAINS` with social/UGC/video domains:
   `facebook.com`, `m.facebook.com`, `fb.com`, `instagram.com`, `twitter.com`, `x.com`,
   `t.me`, `telegram.org`, `youtube.com`, `youtu.be`, `tiktok.com`, `reddit.com`,
   `medium.com`, `substack.com` (review the last two — keep if you want named-author
   blogs out of an institutional product).
2. Promote the blocklist + `_article_domain()` into a shared module
   (`src/georisk_agent/news/source_filter.py`) exporting `is_blocked_url(url) -> bool`,
   and call it from **three** sites: `_normalise_article()` (fetcher), the result loop in
   `web_search.py::search_web()`, and the web-fallback loop in `rag_research_node`
   (skip any chunk whose `url`/`source` domain is blocked **before** `seen.add` and before
   `ingest_web_results`).
3. **Purge the already-poisoned cache.** Add a one-off maintenance query in
   `db/dal.py` (`delete_ephemeral_by_domain(domains)`) and a small script
   `scripts/purge_blocked_ephemeral.py` to delete existing `ephemeral_embeddings` rows
   whose `url`/`source` matches the new blocklist. Without this, the Facebook chunk
   persists until its TTL expires.

**Files/functions:** `news/fetcher.py::_BLOCKED_DOMAINS`, `_article_domain`,
`_normalise_article`; new `news/source_filter.py`; `rag/web_search.py::search_web`;
`agents/nodes_rag_research.py` (web-fallback block); `db/dal.py` (+ new delete query);
new `scripts/purge_blocked_ephemeral.py`.

**Success metric:** Re-run E1, E2, H1, H3. Zero `facebook.com` / social / video URLs in
`sources` or `evidence`. Add a unit test `tests/test_source_filter.py` asserting
`is_blocked_url("https://www.facebook.com/etnow/posts/...")` is `True` and a normal
Reuters URL is `False`.

**Complexity:** **S** (blocklist + shared helper) + **S** (purge script) = **S/M**.

---

#### C2 — No ambiguity detection (vague query gets confident generic answer)
**Evaluation refs:** H3 (scored 4.0/10)
**Root cause:**
`agents/nodes_planner.py::planner_node` unconditionally decomposes any input into 4–6
sub-questions and returns a `plan`. There is no specificity check and no branch that can
short-circuit the pipeline with a clarification request. Downstream nodes have no concept
of "too vague to answer".

**Fix approach:**
1. Add a structured pre-step in `planner_node`. Replace the free-text prompt with a
   `with_structured_output` call returning a Pydantic model:
   `PlannerOutput { is_answerable: bool, ambiguity_reason: str, clarifying_questions: list[str], missing_dimensions: list[str], plan: list[str] }`.
   Prompt rule: a query is **not answerable** when it lacks at least two of
   {specific region/country, specific asset class/sector, specific time horizon, specific
   scenario/trigger}. "Is now a good time to invest in the Global South?" fails on
   region-specificity (a 130-country bloc), asset specificity, and trigger — flag it.
2. Add `needs_clarification: bool` and `clarifying_questions: list[str]` to
   `DynamicAgentState` (`app/types.py`).
3. In `agents/graph.py`, add a conditional edge after `planner`: if
   `state["needs_clarification"]`, route to a new terminal `clarification_node` that
   populates a user-facing "I need more detail" report and skips RAG/Signals/Analysis
   entirely. The API/`build_full_graph` entry path returns this directly; the HITL flow
   already surfaces the plan to the user, so this also gives the frontend the clarifying
   questions to render.
4. Keep a `force_answer` escape hatch (state flag) so the HITL "approve anyway" path can
   bypass the gate.

**Files/functions:** `agents/nodes_planner.py::planner_node` (+ new `PlannerOutput`);
`app/types.py::DynamicAgentState`; `agents/graph.py` (`_add_rag_to_end` / `build_full_graph`,
new `clarification_node` + conditional edge).

**Success metric:** H3 returns `needs_clarification=True` with ≥2 concrete clarifying
questions (e.g. "Which Global South regions — LatAm, Sub-Saharan Africa, South/SE Asia?",
"Which asset class — sovereign debt, equities, FDI?"). Specific queries (E1, M1, H1) still
produce a plan with `needs_clarification=False`. Add `tests/test_planner_ambiguity.py`.

**Complexity:** **M**.

---

### HIGH

---

#### H-A — Unidentified mystery PDFs in the corpus
**Evaluation refs:** appeared in 4/9 queries
**Root cause:**
`scripts/ingest_documents.py` (line ~92) stores `source = file_path.name`, i.e. the raw
filename, with no display title. The two offenders exist on disk:
`data/documents/ar2023e.pdf` (the **BIS Annual Economic Report 2023**) and
`data/documents/sdnea2023001.pdf` (an **IMF Staff Discussion Note**). They are not false
positives in the vector store — they are legitimately ingested but surface under opaque
codes, and because BIS/IMF macro reports are broad they match many queries.

**Fix approach:**
1. Add a curated **source manifest** `data/documents/sources.json` mapping each filename to
   a human-readable `title`, `publisher`, and `doc_type`. Rename the two cryptic files to
   self-describing names (`BIS_Annual_Report_2023.pdf`,
   `IMF_SDN_<topic>_2023.pdf`) — confirm the IMF note's exact topic from its first page
   before naming.
2. In `ingest_documents.py`, load the manifest and store the friendly title in both
   `source` and `metadata` (`metadata = {"file": file_path.name, "title": <display>,
   "publisher": ..., "doc_type": ...}`). Re-run ingestion (the schema is upsert-by
   `chunk_id`, so stale rows under the old names should be deleted first — add a
   `--reset` flag or a small delete-by-source step).
3. Optional relevance guard: because broad macro reports over-match, consider a lower
   per-source chunk cap or a higher `min_similarity` for these specific publishers (see
   M-A below — the same retrieval-precision fix covers this).

**Files/functions:** `scripts/ingest_documents.py::ingest_directory`; new
`data/documents/sources.json`; rename two files on disk.

**Success metric:** Re-run the 4 affected queries. Sources cite human-readable titles
("BIS Annual Economic Report 2023"), never `ar2023e.pdf`. No chunk's `source` matches
`^[a-z]{2,}\d{4}` (cryptic-code regex) — add an assertion to the ingest script.

**Complexity:** **S**.

---

#### H-B — Wrong / under-scoped macro indicators for query context
**Evaluation refs:** M3 (USD + EM bonds → only USA shown), H3 (Global South → Ukraine,
Russia, Poland — all Global North)
**Root cause:**
`agents/nodes_signals.py::extract_relevant_countries` is **literal keyword matching only**.
- "Global South" is not a key in `REGION_TO_ISOS`, so it returns nothing topical; the ISOs
  that *do* appear (UKR/RUS/POL) leak in from `REGION_TO_ISOS["eastern europe"]` matching
  incidental plan text, not the query intent.
- "EM bonds / USD" has no country tokens, so only "US"/"USA" matches → USA-only signals.
- `REGION_TO_ISOS` has no "global south", "brics", "emerging markets", "asean", "latam",
  "sub-saharan", "south asia", etc., and there is no thematic→indicator mapping (EM bond
  queries should pull EMBI/sovereign-spread proxies, not just trade-as-%-GDP).

**Fix approach:**
1. Expand `REGION_TO_ISOS` with the missing blocs: `global south`, `brics`, `emerging
   markets` / `em`, `asean`, `latam` / `latin america`, `sub-saharan africa`, `south asia`,
   `gcc`, `sahel`, `balkans`. Cap each bloc to a representative basket (5–8 ISOs) so the
   World Bank fan-out stays bounded.
2. Add a **thematic indicator selector**. Today `_fetch_country` always fetches
   `trade_gdp` (+ `oil_rents` for producers). Introduce a query-theme classifier (cheap:
   keyword sets for `{energy, fx/dollar, sovereign-debt, trade, defense, semiconductors}`)
   and map themes → World Bank indicator codes (e.g. sovereign-debt → `GC.DOD.TOTL.GD.ZS`
   debt-to-GDP, `FR.INR.RINR` real interest rate; fx → reserves `FI.RES.TOTL.CD`). Drive
   ticker selection from the same theme map so M3 (USD + EM bonds) pulls `DX-Y.NYB`, `EEM`,
   `EMB`/`EMHY` and EM FX, not USA trade.
3. Add a **scope-mismatch guard**: if the detected ISOs are all Global North but the query
   text contains a Global South / EM token, drop the Global-North leakage. Log the
   detected-vs-expected scope so it is auditable in `debug`.

**Files/functions:** `agents/nodes_signals.py` — `REGION_TO_ISOS`, `COUNTRY_TICKERS`,
`build_tickers`, `_fetch_country`, `signals_node`, `extract_relevant_countries` (+ new
theme classifier + indicator map).

**Success metric:** M3 shows USD index + EM bond/FX indicators (not USA trade). H3 (if it
passes the C2 ambiguity gate with clarification, otherwise its specific successor) shows
Global South basket countries only — zero Global-North ISOs. Add
`tests/test_signals_scope.py` covering both cases.

**Complexity:** **M/L**.

---

#### H-C — RAG retrieval noise (false-positive corpus hits)
**Evaluation refs:** M1 (`us_china_trade.txt` retrieved for a Brazil/Indonesia FDI query)
**Root cause:**
`rag/retriever.py::retrieve` uses a single global floor `min_similarity=0.35`. At 0.35,
trade-themed documents superficially match any trade query. Also `rag_research_node` calls
`retrieve(sq, k=5)` with the **default** floor (0.35), not a stricter one — the 0.55
"well-answered" threshold is only used downstream to decide whether to fire the web
fallback, not to filter what enters `retrieved_chunks`. So low-relevance chunks are still
shown to the Analysis LLM and cited.

**Fix approach:**
1. Raise the effective retrieval floor used by `rag_research_node`: call
   `retrieve(sq, k=5, min_similarity=0.45)` (tune empirically) so genuinely tangential hits
   are dropped at source rather than merely flagged.
2. Add a **post-retrieval relevance trim** in `rag_research_node`: after collecting
   historical chunks, drop any whose `similarity` is below a per-query adaptive floor
   (e.g. `max(0.45, top_similarity - 0.12)`) so a strong query doesn't get padded with weak
   chunks. Keep at least the single best chunk so the sub-question is still "answered".
3. (Pairs with M-A.) Down-weight broad-macro publishers so they cannot dominate a
   country-specific query.

**Files/functions:** `rag/retriever.py::retrieve` (expose/raise floor),
`agents/nodes_rag_research.py` (`_fetch_historical`, post-retrieval trim,
`_WELL_ANSWERED_MIN_SIM` reuse).

**Success metric:** M1 no longer cites `us_china_trade.txt`; Brazil/Indonesia chunks
dominate. Average `avg_cosine_distance` across the eval set drops; `thin_evidence`
correctly flips True for genuinely uncovered sub-questions instead of being masked by
padding.

**Complexity:** **M**.

---

#### H-D — Inconsistent web-search activation
**Evaluation refs:** M1 (17 sources), M2 (7), M3 (3, no web search at all)
**Root cause:**
In `rag_research_node`, the web fallback fires **only** for sub-questions where
`not raw_live.get(sq) and not any(similarity >= 0.55 ...)`. When the corpus returns a
mediocre 0.55+ hit, the sub-question is deemed "well answered" and Tavily never runs —
even if the hit is a generic macro report (the exact M3 failure mode). Result: query
richness depends entirely on incidental corpus coverage, not on actual evidence quality.

**Fix approach:**
1. Decouple "has a chunk" from "is well covered". Define coverage by **count and
   relevance**, not a single 0.55 hit: a sub-question is well-covered only if it has
   ≥2 historical chunks at ≥0.55 **or** ≥1 live chunk. Otherwise fire the web fallback.
2. Set a **minimum web-research floor per query**: if total non-web chunks across the whole
   plan are below a threshold (e.g. <6) or fewer than half the sub-questions are
   well-covered, run Tavily for the weakest N sub-questions regardless. This guarantees
   M3-style queries get web research.
3. Make `max_results` adaptive (3 default; 5 when the corpus is thin) and log the
   activation decision per sub-question in `debug` for auditability.

**Files/functions:** `agents/nodes_rag_research.py` (web-fallback block, the
`unanswered_sqs` predicate and `_WELL_ANSWERED_MIN_SIM` logic).

**Success metric:** Source counts across the eval set fall within a tighter band (e.g.
6–18, none ≤3 unless the query is genuinely narrow). M3 gets web chunks. Track per-query
web-activation in `debug`.

**Complexity:** **M**.

---

#### H-E — No portfolio-level net synthesis
**Evaluation refs:** H1 (Aramco + TSMC + EU defense ETF — positions partially hedge but no
net verdict)
**Root cause:**
`agents/nodes_analysis.py::_run_portfolio_analysis` produces one `PortfolioHoldingImpact`
**per holding independently** and stops. There is no roll-up pass that nets opposing
positions (e.g. an oil shock is bullish Aramco, bearish a chip consumer, bullish defense —
the portfolio is partially hedged). `portfolio_impacts` is a flat list; nothing computes a
net stance.

**Fix approach:**
1. Add a `PortfolioNetSynthesis` schema:
   `{ net_verdict: Literal["Net Bullish","Net Bearish","Net Hedged/Neutral"],
   net_reasoning: str, dominant_exposures: list[str], offsetting_pairs: list[str],
   concentration_warnings: list[str], suggested_hedge_adjustments: list[str] }`.
2. Add a final synthesis call in `analysis_node` (after `portfolio_impacts` is finalized
   and after deterministic enforcement), `_run_portfolio_net_synthesis(portfolio_impacts,
   market_impacts, impact_vectors, portfolio_prices)`. Weight by `cost_basis_usd` /
   `quantity * price` when available so the net verdict reflects position sizing, not a
   simple vote count.
3. Store as `state["portfolio_net"]` (add to `DynamicAgentState`) and render it as the
   headline of the portfolio section in the API/frontend.

**Files/functions:** `agents/nodes_analysis.py` (new `_run_portfolio_net_synthesis`,
wired into `analysis_node` after enforcement); `app/types.py` (`portfolio_net` field);
frontend portfolio view.

**Success metric:** H1 returns a net verdict naming the hedge (Aramco long-oil vs.
chip-consumer short-oil) and a concentration/offsetting note. Add
`tests/test_portfolio_net.py` with a synthetic hedged portfolio asserting
`net_verdict == "Net Hedged/Neutral"`.

**Complexity:** **M**.

---

#### H-F — Self-contradictory scenario not caught
**Evaluation refs:** M2 ("If tensions **stabilize**, semiconductor stocks decline 20%" —
stabilization is the recovery case, so a decline is internally contradictory)
**Root cause:**
The Consistency Validator (`agents/nodes_consistency.py`) only validates **portfolio
verdicts vs. takeaway/market-impacts direction**. It never inspects the `scenarios` text
for internal logic. The Reviewer (`agents/nodes_reviewer.py`) checks evidence sufficiency
and RAG-vs-live contradictions, not scenario self-consistency. Neither node reads the
direction word ("stabilize"/"escalate") against the projected move sign.

**Fix approach:**
1. Add a deterministic scenario-polarity check (cheapest, runs first). In a new helper
   `verdict_rules.py::check_scenario_polarity(scenarios) -> list[str]`: detect the scenario
   label ("base"/"stabiliz*"/"de-escalat*" vs "escalation"/"escalat*") and the directional
   move (regex for "+/-N%", "decline/rise/spike/drop"). Flag when a stabilization/base
   scenario pairs with a risk-asset **decline** or an escalation pairs with a risk-asset
   **rally** (sign-mismatch). Equities/risk assets fall on escalation and recover on
   stabilization; safe havens (gold, VIX, USD) invert.
2. Extend `consistency_validator_node` to also pass `scenarios` to a focused LLM check (or
   reuse the existing call) that rewrites any flagged scenario so its direction matches its
   label. Add `scenario_corrections` to the `debug` block.
3. Add the same scenario polarity to the Reviewer's contradiction list so a severe case can
   trigger a retry.

**Files/functions:** `agents/verdict_rules.py` (new `check_scenario_polarity`);
`agents/nodes_consistency.py::consistency_validator_node` (scenario pass);
`agents/nodes_reviewer.py` (surface as contradiction).

**Success metric:** The M2 contradiction is auto-corrected (stabilization → recovery/upside,
or the figure flipped) and logged in `debug.scenario_corrections`. Add
`tests/test_scenario_polarity.py` feeding the exact M2 string and asserting a flag.

**Complexity:** **M**.

---

### MEDIUM

---

#### M-A — Templated scenario values not anchored to live market data
**Evaluation refs:** E1 & E2 (identical $80–85 base / $90–100 escalation despite different
queries; live Brent $75.73 ignored)
**Root cause:**
`analysis_node` already extracts **query** price anchors (`extract_price_benchmarks`) and
builds a `benchmark_block`, but when the query states **no** explicit price it does **not**
inject the **live** market price as the scenario baseline. The `MARKET_INSIGHT_RULES`
"PRICE BASELINE HIERARCHY" mentions Priority 2 = live price, but the live `signals`
`market_data` values are placed in a generic `signals_block`, not surfaced as a mandated
scenario anchor. With no hard anchor and temperature 0.2, the LLM falls back to memorized
round numbers ($80–85 / $90–100).

**Fix approach:**
1. In `analysis_node`, when a commodity/index is implicated by the query but has no
   explicit query benchmark, build a **LIVE PRICE ANCHORS** block from `signals["market_data"]`
   (e.g. "Brent base case must project from the live $75.73, not a memorized $80"). Make it
   non-negotiable in the prompt, mirroring `benchmark_block`.
2. Add a deterministic post-check: parse the dollar figures out of `scenarios`; if the base
   case for an anchored commodity deviates from the live price by more than a tolerance
   (e.g. >10% with no stated catalyst), log a warning in `debug` and optionally re-prompt.

**Files/functions:** `agents/nodes_analysis.py::analysis_node` (new live-anchor block,
reuse `extract_price_benchmarks` shape); optional post-check helper in `verdict_rules.py`.

**Success metric:** E1 and E2 produce **different**, live-anchored scenario ranges; the
base case sits within ~10% of the live Brent print. Add a test asserting two distinct
queries with the same live feed don't yield byte-identical scenarios.

**Complexity:** **S/M**.

---

#### M-B — Wrong mechanism: geographic proximity conflated with supply-chain exposure
**Evaluation refs:** H1 (TSMC marked as facing "production delays" from Iran-Israel; TSMC
has no Middle East exposure — the real chain is oil shock → risk-off → elevated Taiwan
risk via China opportunism)
**Root cause:**
The portfolio prompt in `_run_portfolio_analysis` has detailed commodity producer/consumer
rules but **no rule distinguishing direct operational exposure from second-order
macro/sentiment exposure**. The LLM defaults to "this asset is near/related to the conflict
region → direct disruption," inventing a supply-chain link that doesn't exist.

**Fix approach:**
1. Add an **EXPOSURE-CHANNEL CLASSIFICATION** block to the portfolio prompt
   (`_run_portfolio_analysis`) requiring each holding to be tagged with its transmission
   channel **before** the verdict: `{direct-operational, supply-chain-input,
   commodity-price, macro-risk-sentiment, none}`. Explicit rule: do **not** assert
   operational/production disruption unless the holding has assets, suppliers, or revenue in
   the affected geography. For geographically distant assets, the legitimate channel is
   macro/risk-off (and, for Taiwan specifically, the "US-distraction → China opportunism"
   second-order channel).
2. Add a worked counter-example to the prompt (the exact TSMC/Iran case) so the model
   learns the distinction.

**Files/functions:** `agents/nodes_analysis.py::_run_portfolio_analysis` (prompt).

**Success metric:** H1's TSMC reasoning cites a macro/risk-off or Taiwan-Strait channel,
not Middle East "production delays". Spot-check via the `analysis_reasoning` debug field.

**Complexity:** **S**.

---

#### M-C — Missing key domain knowledge (chokepoints, second-order effects)
**Evaluation refs:** Strait of Hormuz absent (E2), capital controls/asset seizure absent
(E1 Russia), ASML absent (M2 semis), TurkStream absent (H2), second-order effects absent
in H2 despite being explicitly requested.
**Root cause:**
Two gaps: (a) the **corpus** lacks targeted documents on these specifics, and (b) the
**Planner** does not guarantee a sub-question on chokepoints/second-order effects, so the
Analysis LLM is never steered toward them.

**Fix approach:**
1. **Planner steering** (cheap, do first): in the Planner prompt, require one sub-question
   on **physical/financial chokepoints** (straits, pipelines, payment rails, export
   controls) and one explicitly on **second-order effects**. This directly fixes the H2
   "second-order effects missing despite being requested" failure.
2. **Domain checklist injection:** add a small static map in `analysis_node` from detected
   topic → must-consider entities (Middle East oil → Strait of Hormuz; Russia sanctions →
   capital controls, asset seizure, reserve freezes; semiconductors → ASML, TSMC, export
   controls; Turkey/Balkans energy → TurkStream, Bosphorus). Inject as a "MUST ADDRESS IF
   RELEVANT" prompt block so the model can't omit the obvious chokepoint.
3. **Corpus expansion** (slower): add targeted documents (IEA/EIA chokepoint reports,
   ASML/export-control briefs, pipeline maps) and re-ingest. Track as a backlog item.

**Files/functions:** `agents/nodes_planner.py` (prompt rules); `agents/nodes_analysis.py`
(domain checklist block); `data/documents/` + `scripts/ingest_documents.py` (corpus).

**Success metric:** E2 mentions Hormuz; E1 mentions capital controls/asset seizure; M2
mentions ASML; H2 includes TurkStream **and** a dedicated second-order-effects section.

**Complexity:** **S** (prompt steering) + **M/L** (corpus expansion).

---

#### M-D — YouTube / non-credible video sources
**Evaluation refs:** H1 (YouTube URL as source #8)
**Root cause:** Same as C1 — no video/social domain filter on the web-fallback and news
paths. Fully covered by the C1 `_BLOCKED_DOMAINS` extension and the shared
`is_blocked_url` helper applied to the Tavily path.

**Fix approach:** Folded into **C1** (add `youtube.com`, `youtu.be`, `tiktok.com`).

**Success metric:** No `youtube.com` / `youtu.be` URLs in any query's sources. Covered by
the C1 unit test.

**Complexity:** **S** (no extra work beyond C1).

---

## Implementation Phases

### Phase 1 — Input hygiene & cheap wins (land first)
- **C1 / M-D** — extend `_BLOCKED_DOMAINS`, shared `is_blocked_url`, apply to Tavily + news
  paths, purge poisoned ephemeral cache. _(S/M)_
- **H-A** — source manifest + rename the two cryptic PDFs + re-ingest with titles. _(S)_
- **M-A** — live-price scenario anchoring. _(S/M)_
- **M-B** — exposure-channel classification block. _(S)_
- **M-C step 1** — Planner chokepoint + second-order-effects sub-question requirements. _(S)_

_Rationale: highest defect-coverage per unit effort, no architectural change, immediately
visible in re-run sources/scenarios._

### Phase 2 — Context scoping & retrieval precision
- **H-B** — region/bloc expansion + thematic indicator selection + scope-mismatch guard. _(M/L)_
- **H-C** — retrieval floor + adaptive post-retrieval trim. _(M)_
- **H-D** — coverage-based, count-aware web-fallback activation + minimum web floor. _(M)_
- **M-C step 2** — domain checklist injection in Analysis. _(S)_

### Phase 3 — Synthesis depth & gating
- **C2** — Planner ambiguity gate + `clarification_node` + state fields + conditional edge. _(M)_
- **H-E** — portfolio net-synthesis pass + `portfolio_net` state + frontend headline. _(M)_
- **H-F** — scenario-polarity check (deterministic + LLM rewrite + reviewer surface). _(M)_

### Phase 4 — Corpus & evaluation hardening (ongoing)
- **M-C step 3** — corpus expansion (chokepoints, ASML, pipelines, capital controls). _(M/L)_
- Extend `evaluation/run_eval.py` with regression assertions for every fix above so the
  next eval run measures movement off the 6.1 baseline.

---

## Success Metrics — Roll-up

| ID | Fix | Verification |
|----|-----|--------------|
| C1/M-D | Social/video source block | Zero facebook/youtube/social URLs across E1,E2,H1,H3; unit test on `is_blocked_url`; ephemeral cache purged |
| C2 | Ambiguity gate | H3 → `needs_clarification=True` + ≥2 clarifying Qs; specific queries unaffected |
| H-A | Corpus relabeling | No cryptic-code `source`; titles cited ("BIS Annual Economic Report 2023") |
| H-B | Indicator scoping | M3 shows USD/EM-bond indicators; H3 successor shows Global-South basket only |
| H-C | Retrieval precision | M1 drops `us_china_trade.txt`; lower avg cosine distance |
| H-D | Web activation | Source counts in 6–18 band; M3 gets web chunks; per-query log |
| H-E | Net portfolio | H1 returns net hedged verdict + offsetting note |
| H-F | Scenario polarity | M2 contradiction auto-corrected + logged |
| M-A | Live anchoring | E1≠E2 scenarios; base within ~10% of live Brent |
| M-B | Exposure channel | TSMC reasoning uses macro/risk-off, not "production delays" |
| M-C | Domain knowledge | Hormuz (E2), capital controls (E1), ASML (M2), TurkStream + 2nd-order (H2) |

**Overall target:** re-run the 9-query eval; lift average from **6.1 → ≥7.5**, with H3
(the 4.0 outlier) either correctly gated as ambiguous or, with a specific successor query,
scoring ≥6.5.

---

## Complexity Summary

| Complexity | Items |
|-----------|-------|
| **S** | H-A, M-B, M-C(step 1), M-D, (C1 blocklist) |
| **S/M** | C1 (with purge), M-A |
| **M** | C2, H-C, H-D, H-E, H-F, M-C(step 2 checklist) |
| **M/L** | H-B, M-C(step 3 corpus) |

_No fix in this plan is rated L on its own; the only large efforts are corpus expansion
(M-C step 3) and the indicator-scoping work (H-B), both of which can be staged incrementally._
