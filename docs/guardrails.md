# Guardrail Registry & Stability Notes

**Status:** This file records the current state of the deterministic guardrail system as of
the P2a–P2e archetype work, the M1 / M2 / M2.5 reset milestones, and Phase 2A/2A.1
stabilization. Phase 2A added regression snapshot tests for the three benchmark cases plus a
targeted risk probe (`tests/test_phase2a_benchmark_snapshots.py`); Phase 2A.1 closed the one
confirmed gap that probe surfaced (see [Tracked stabilization risks](#5-tracked-stabilization-risks)).
Everything else described as "overlapping" or "duplicated" below is still unmerged, undeleted,
undeprecated, and un-disabled — see
[Dangerous cleanup candidates](#8-dangerous-cleanup-candidates--do-not-touch-yet) and
[Tests required before cleanup](#7-tests-required-before-cleanup) for what must happen
before any of that changes.

Line numbers below are anchors from a read-only audit pass, not guaranteed-exact — re-grep
symbol names before citing them in a commit or PR.

---

## 1. Pipeline execution order

```
planner_node
   │ (conditional _after_planner)
   ├─ is_answerable == False → clarification → END
   └─ else → rag_research_node
                 │
              signals_node
                 │
              analysis_node                 ← Phase 1 macro analysis: market_impacts,
                 │                             risks, scenarios, investor_takeaway,
                 │                             confidence, event_materiality, event_type;
                 │                             scrub_numeric_ranges() on macro prose
              macro_context_node
                 │ (conditional fan-out: spawn_ticker_workers)
                 ├─ no enriched_portfolio → consistency_validator_node (skip fan-out)
                 └─ else → Send() per holding
                             │
                       ticker_analyst_node   ← parallel fan-out, one worker per holding
                       (N concurrent workers)   (LLM + RULE 0A–RULE 10 prompt; deterministic
                                                  shortcuts: UNRECOGNIZED_TICKER placeholder,
                                                  low-materiality-no-exposure shortcut)
                             │  (all workers converge)
                       reduce_ticker_results_node   ← deterministic reducer guardrail chain
                             │                          (see registry table, section 2)
                             │                          + portfolio_net synthesis
                             │                          + portfolio takeaway generation
                             │
                       consistency_validator_node   ← deterministic pre-pass + LLM
                             │                          correction + invariant re-seal +
                             │                          conditional takeaway regeneration
                             │
                       reviewer_node                ← LLM review; RETRY vs continue
                             │ (conditional should_continue)
                             ├─ verdict == RETRY & retries left → back to rag_research_node
                             └─ else → final_output_node
                                            │  enforce_macro_confidence_risk_caps()
                                            │  (disclaimer prepend, per-ticker risk cap,
                                            │   portfolio_net.net_confidence cap)
                                            │  strips reviewer_verdict
                                          END
```

`evaluation/assertions.py` is **not wired into `graph.py`**. It is invoked only by the
eval/benchmark harness (`run_eval.py`) and `tests/test_eval_assertions.py`, against the
final response object after the graph has already completed — it is a scoring/regression
tool, not a pipeline node.

`reviewer_node` was not deep-dived in the audit beyond its retry-routing behavior. **Verify**
it doesn't itself mutate verdict/risk_score/confidence before relying on this diagram for
anything load-bearing (see [Uncertainties](#9-uncertainties--verify-before-cleanup)).

---

## 2. Guardrail registry table

Legend: V = verdict/market_sentiment, R = risk_score/confidence, P = prose (6 fields:
`short_term_analysis`, `long_term_analysis`, `short_term_impact`, `long_term_impact`,
`causal_reasoning`, `reasoning`), T = investor_takeaway.

| Name | File:Function | When it runs | Reads | Writes | V | R | P | T | Milestone (best guess) | Class | Overlaps with | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `enforce_asset_class_verdicts` (VIX invariant + index alignment) | `verdict_rules.py:148–262` | reduce step 1; re-run as consistency pre-pass; re-run again post-LLM in consistency | impacts[].verdict/market_sentiment/reasoning/prose, ticker | verdict, market_sentiment, reasoning, causal_reasoning, prose via `_sync_prose_to_verdict` | ✓ | ✗ | ✓ | ✗ | Reset M1 (prose/verdict consistency) | Architectural (invariant law) | Runs 3× in one request (reduce, CV pre-pass, CV post-LLM) | Keep but document — the 3× re-entry is intentional sealing, not a bug |
| `_sync_prose_to_verdict` | `verdict_rules.py:111–128` | helper, called by nearly every verdict-mutating rule | p dict | 4 prose fields (short/long analysis+impact aliased) | ✗ | ✗ | ✓ | ✗ | Reset M1 | Architectural (shared primitive) | N/A — this is the de-duplication point | Keep — canonical "if you change verdict, call this" contract |
| `enforce_defense_contractor_verdicts` | `verdict_rules.py:674–742` | reduce step 2 | impacts[].ticker/verdict/causal_reasoning/prose | market_sentiment, verdict, causal_reasoning, reasoning, prose | ✓ | ✗ | ✓ | ✗ | Pre-dates listed milestones (early tactical ticker-list patch) | Tactical (hardcoded ticker frozenset) | **Duplicates** defense branch of `enforce_archetype_bounds` Pass 2 (identical `_DEFENSE_ESCALATION_RE`, identical Bullish→Neutral cap) | Keep but document — see [Known overlaps](#4-known-overlaps) |
| `enforce_archetype_bounds` (Pass 1: forbidden-prose scrub; Pass 2: defense verdict cap) | `verdict_rules.py:761–914` | reduce step 3 | impacts[], enriched_portfolio (archetype), forbidden_prose_patterns per archetype | prose (Pass 1), verdict/market_sentiment (Pass 2, defense_contractor only) | ✓ (defense only) | ✗ | ✓ | ✗ | P2a–P2d archetype system | Architectural (generalized, archetype-driven) | Pass 1 overlaps `_apply_nvda_prose_guard` for NVDA; Pass 2 overlaps `enforce_defense_contractor_verdicts` | Keep — long-term owner for both defense-contractor and NVDA-style prose bounds |
| `detect_takeaway_misalignments` | `verdict_rules.py:290–383` | reduce step 4; re-run in consistency pre-pass | impacts[], investor_takeaway, `balanced_vector_calibrated` flag | verdict, market_sentiment, prose | ✓ | ✗ | ✓ | ✗ (reads T, doesn't write it) | Pre-dates P2e, best guess | Tactical | Runs twice per request (reduce + CV pre-pass) | Keep but document double-invocation |
| `apply_trade_policy_balanced_verdict` (rules T4–T11) | `verdict_rules.py:1105–1314` | reduce step 5, only when `event_type == "trade_policy_tariff"` | impacts[].exposure_vectors/archetype/verdict, **`enriched_portfolio` (Phase 2A.2)** | verdict, market_sentiment, prose, `balanced_vector_calibrated`, `balanced_vector_rule` flags | ✓ | ✗ | ✓ | ✗ | P2e trade-policy exposure vectors; archetype-resolution wiring fixed Phase 2A.2 | Architectural (P2e core deliverable) | No direct duplicate | Keep — see [Tracked stabilization risks](#5-tracked-stabilization-risks) for the Phase 2A.2 fix history |
| `_apply_risk_score_caps` | `nodes_reduce.py` (~170–226) | reduce step 6 | impacts[].exposure_channel, risk_score, event_materiality | risk_score | ✗ | ✓ | ✗ | ✗ | Reset M2.5 (ticker hygiene) | Tactical | Overlaps conceptually with RULE 9 prompt instruction (same rule, enforced twice) | Keep — correct ticker-level owner |
| `_apply_nvda_prose_guard` | `nodes_reduce.py` (~86–137) | reduce step 7, NVDA only | impacts[].ticker=="NVDA", short/long_analysis, causal_reasoning | prose (3 fields + legacy aliases) | ✗ | ✗ | ✓ | ✗ | Pre-dates archetype system, best guess | Legacy/tactical (ticker-hardcoded, not archetype-driven) | **Triplicated** with archetype `fabless_ai_chip_designer.forbidden_prose_patterns`, RULE 8 NVIDIA prompt text, and `nodes_macro_context.py`'s `_TICKER_BUSINESS_MODEL_HINTS["NVDA"]` | Candidate for deletion after regression tests — strongest single consolidation candidate in the registry |
| `_flag_numeric_precision_violations` | `nodes_reduce.py` (~229–257) | reduce step 8 | impacts[] prose | none (detection/logging only) | ✗ | ✗ | ✗ | ✗ | — | Tactical (diagnostic) | Detects same regex family as `scrub_numeric_ranges` but doesn't fix | Keep — cheap, detection-only |
| `_scrub_ticker_numeric_prose` (→ `scrub_numeric_ranges` → `_scrub_one`) | `nodes_reduce.py` (~140–167); core in `verdict_rules.py:526–653` | reduce step 9 (per-ticker); also called directly in `analysis_node` for macro fields | prose fields (3 ticker-level; 4 macro-level) | prose fields, legacy aliases | ✗ | ✗ | ✓ | ✓ (macro takeaway only, in `analysis_node`) | Reset M1/M2 era, best guess | Architectural (shared scrub primitive) | See [Numeric scrubber risks](#6-numeric-scrubber-risks) | Keep — flag incomplete verb coverage as documented known-gap |
| `enforce_low_materiality_no_exposure_neutrality` | `verdict_rules.py:1004–1079` | reduce step 10 | impacts[], event_materiality, event_type, macro_context, geographic/commodity/archetype relevance | verdict, market_sentiment, risk_score, confidence, exposure_channel, prose, `low_materiality_neutralized` flag, `low_materiality_rule` flag | ✓ | ✓ | ✓ | ✗ | Reset M2.5 | Architectural (P2/M2.5 core deliverable) | No direct duplicate — sole owner of the low-materiality-no-exposure seal | Keep — reference pattern for CV-protection design |
| `_run_portfolio_net_synthesis` | `nodes_analysis.py:672–752` | reduce step 11; recomputed again in consistency_validator_node after LLM correction | impacts[].verdict, macro_confidence | portfolio_net (net_verdict, net_confidence, counts) | ✗ (reads only) | ✓ (net_confidence) | ✗ | ✗ | Reset M2 | Architectural | Recomputed 2–3× per request (reduce, CV, and capped again in final_output_node) | Keep but document recomputation chain |
| `_build_portfolio_takeaway` (incl. coverage gap-fill, low-materiality grounded short-circuit) | `nodes_analysis.py:892–1085` | reduce step 12; re-run from consistency_validator_node if `fixed_count > 0` | impacts[], enriched_portfolio, macro_takeaway | investor_takeaway | ✗ (reads verdict only) | ✗ | ✗ | ✓ | P2e / Reset M2.5 (grounded takeaway) | Architectural | Coverage gap-fill could restate a ticker inconsistently with a verdict CV changes without triggering regen | Keep but document; verify `fixed_count > 0` covers all CV correction paths |
| `consistency_validator_node` (pre-pass, LLM correction, post-LLM reseal) | `nodes_consistency.py:95–363` | after reduce, before reviewer | portfolio_impacts, investor_takeaway, market_impacts, scenarios, enriched_portfolio | portfolio_impacts, portfolio_net, investor_takeaway | ✓ | ✗ (doesn't touch risk_score directly) | ✓ (via `_strip_stale_verdict_refs`) | ✓ (regen) | Reset M1 (consistency validation) | Architectural | Re-invokes `enforce_asset_class_verdicts` and `detect_takeaway_misalignments`; protects `low_materiality_neutralized` but not `balanced_vector_calibrated` in its LLM-correction branch | Keep but document the flag-protection asymmetry — see [Tracked stabilization risks](#5-tracked-stabilization-risks) |
| `enforce_macro_confidence_risk_caps` | `verdict_rules.py:1349–1450`, called from `final_output_node` (`graph.py:111–139`) | very last step before END | portfolio_impacts, portfolio_net, investor_takeaway, macro_confidence | risk_score (per ticker, capped), confidence, portfolio_net.net_confidence, investor_takeaway (disclaimer prepend) | ✗ | ✓ | ✗ | ✓ (disclaimer only) | Reset M2 | Architectural (M2 core deliverable) | Layered on top of `_apply_risk_score_caps` (ticker-level) as final macro-level seal | Keep — final authority for macro-level risk/confidence display |

---

## 3. Ownership model

| Concern | Long-term owner |
|---|---|
| Archetype behavior | `enforce_archetype_bounds` + `archetypes.py` |
| Trade-policy calibration | `apply_trade_policy_balanced_verdict` |
| Low-materiality no-exposure | Pre-LLM shortcut (`_should_use_low_materiality_no_exposure_shortcut` / `_low_materiality_no_exposure_entry` in `nodes_ticker_analyst.py`) **+** post-LLM seal (`enforce_low_materiality_no_exposure_neutrality` in `verdict_rules.py`) |
| Ticker-level risk caps | `_apply_risk_score_caps` |
| Final macro/display risk caps | `enforce_macro_confidence_risk_caps` |
| Prose sync | `_sync_prose_to_verdict` |

This table states the *intended* long-term owner per concern. Where a second, overlapping
implementation currently also exists (defense-contractor logic, NVDA prose), that overlap is
documented in section 4 and is **not** removed by this document.

---

## 4. Known overlaps

Documented as-is. None of these have been merged or removed.

### Defense contractor logic

Three places implement defense-contractor de-escalation logic, sharing the same
`_DEFENSE_ESCALATION_RE` regex and the same "Bullish + no escalation signal → Neutral" rule:

1. `enforce_defense_contractor_verdicts()` — `verdict_rules.py:674–742`, keyed off a
   hardcoded ticker frozenset (`_DEFENSE_CONTRACTOR_TICKERS`: LMT, RTX, NOC, GD, HII, LDOS,
   BA, BAESY, L3HT).
2. `enforce_archetype_bounds()` Pass 2 — `verdict_rules.py:875–910`, keyed off
   `archetype_id == "defense_contractor"` (archetype-driven, covers any ticker mapped to
   that archetype, not just the hardcoded list).
3. RULE 8 "GOVERNMENT CONTRACTORS" section — `nodes_ticker_analyst.py` prompt text
   (~lines 249–268), prompt-level guidance, not enforced code.

Both #1 and #2 run in the reduce node in sequence (steps 2 and 3), so the same check
effectively executes twice per request. `enforce_archetype_bounds` Pass 2 is the intended
long-term owner (section 3); `enforce_defense_contractor_verdicts` remains as a
belt-and-suspenders tactical backup until the defense-contractor equivalence test in
section 7 exists.

### NVDA logic

Four overlapping places touch NVDA-specific prose/classification:

1. RULE 8 "NVIDIA-SPECIFIC FACTS" — `nodes_ticker_analyst.py` (~lines 196–208), prompt-level
   guidance fed to the LLM.
2. `archetypes.py` `fabless_ai_chip_designer.forbidden_prose_patterns` (~lines 130–138),
   enforced via `enforce_archetype_bounds()` Pass 1 in the reduce node (step 3) — NVDA maps
   to this archetype via `TICKER_ARCHETYPE_MAP["NVDA"]`.
3. `_apply_nvda_prose_guard()` — `nodes_reduce.py` (~lines 86–137), a ticker-hardcoded
   (not archetype-driven) scrub that runs at reduce step 7, i.e. after the archetype-level
   scrub has already run at step 3.
4. `_TICKER_BUSINESS_MODEL_HINTS["NVDA"]` in `nodes_macro_context.py` (~lines 40–69) — feeds
   macro_context, upstream of ticker_analyst, duplicating the same "fabless, don't say
   production capacity" facts a third/fourth time as a prompt-injection input.

Steps 2 and 3 both scrub essentially the same forbidden phrase family for the same ticker,
in the same pipeline stage, back to back. Whether `_apply_nvda_prose_guard` can be safely
replaced by the archetype-level guard alone is **not yet verified** — the two pattern lists
were reported separately and have not been diffed line-by-line (see section 9).

### Repeated invocation of `enforce_asset_class_verdicts`

Runs 3× per request: reduce step 1, consistency-validator pre-pass, and again post-LLM
inside `consistency_validator_node` as a re-seal. This appears to be intentional (guards
against the CV's own LLM step violating the VIX invariant) rather than accidental
duplication, but it has not been explicitly documented anywhere else in the codebase until
now.

### Repeated invocation of `detect_takeaway_misalignments`

Runs 2× per request: reduce node (step 4) and again in the consistency-validator's
deterministic pre-pass.

---

## 5. Tracked stabilization risks

### `balanced_vector_calibrated` protection asymmetry — CONFIRMED and FIXED (Phase 2A / 2A.1)

- `low_materiality_neutralized` **is** explicitly checked and protected inside
  `consistency_validator_node`'s LLM-correction branch (`nodes_consistency.py` ~line 257).
- `balanced_vector_calibrated` **is respected by `detect_takeaway_misalignments`**
  (`verdict_rules.py` ~line 342: `if p.get("balanced_vector_calibrated"): continue`), which
  runs in the reduce node and in the consistency-validator's own deterministic pre-pass.
- Phase 2A added a targeted regression test
  (`tests/test_phase2a_benchmark_snapshots.py::TestBalancedVectorCalibratedConsistencyProtection`)
  that proved this asymmetry was a **live bug, not a theoretical gap**: a P2e-calibrated
  Neutral verdict (e.g. rule T10) was silently overwritten to Bullish by
  `consistency_validator_node`'s LLM-correction branch, because that branch checked only
  `low_materiality_neutralized`, not `balanced_vector_calibrated`.
- Phase 2A.1 closed the gap with a minimal, targeted fix in `nodes_consistency.py`: the
  branch's skip-condition now reads
  `if ticker_upper in correction_map and (p.get("low_materiality_neutralized") or
  p.get("balanced_vector_calibrated")): ... skip correction`, and the log message reports
  whichever of `low_materiality_rule` / `balanced_vector_rule` is set. No other file changed
  (no prompt edits, no changes to LLM response parsing, P2e calibration logic,
  `detect_takeaway_misalignments`, or `apply_trade_policy_balanced_verdict`).
- The regression test above now passes and locks this protection in going forward. Full
  suite: 508 passed, 0 failed after the fix.

### P2e T10/T11 archetype-resolution wiring gap — CONFIRMED and FIXED (Phase 2A.2)

- Manual QA on the EU Chinese EV tariffs benchmark found `BMW.DE` and `VWAGY` remained
  Bullish instead of being calibrated to Neutral by T10, contradicting the Phase 2A
  snapshot's expectation.
- Root cause: `apply_trade_policy_balanced_verdict()` read `p.get("archetype")` directly off
  each ticker's own impact dict, but the real `TickerHoldingAnalysis` schema
  (`schemas_portfolio.py:188–269`) has **no `archetype` field at all** — so in the real
  reduce-node production path, `archetype` was always `None` for every holding, meaning
  T10/T11 (both archetype-gated) could never fire for *any* ticker, not just BMW.DE/VWAGY.
  Every prior P2e/Phase 2A test passed only because their hand-built fixtures injected
  `"archetype": "automaker"` etc. directly onto the impact dict — a shape the real pipeline
  never produces. (T4/T5/T6/T7/T9 are archetype-agnostic and were unaffected.)
- Phase 2A.2 fix: `apply_trade_policy_balanced_verdict()` now accepts an optional
  `enriched_portfolio` parameter (`verdict_rules.py`) and resolves archetype with precedence
  `p.get("archetype") or archetype_by_ticker.get(ticker_upper)`, mirroring how
  `enforce_archetype_bounds()` already resolves archetype from `enriched_portfolio`.
  `reduce_ticker_results_node` (`nodes_reduce.py`) now passes `enriched_portfolio` into the
  call. Existing calls with only `(impacts, event_type)` remain valid — the new parameter
  defaults to `None`.
- New regression tests added in `tests/test_p2e_trade_policy.py`
  (`TestArchetypeResolutionFromEnrichedPortfolio`) prove T10/T11 fire when archetype comes
  *only* from `enriched_portfolio` (no `archetype` key on the impact dict at all — the true
  production shape), and that direct dict-level `archetype` still takes precedence for
  backward compatibility. `tests/test_phase2a_benchmark_snapshots.py`'s EU snapshot fixture
  was also updated to drop the hand-injected `archetype` key, so it now exercises the real
  wiring path end-to-end instead of the optimistic shortcut.
- **Reported separately, then fixed in Phase 2A.3:** even with the wiring fix, `BMW.DE` and
  `VWAGY` specifically still failed to resolve an archetype, because `TICKER_ARCHETYPE_MAP`
  (`archetypes.py`) had no `.DE`-suffix normalization and no `VWAGY` entry (only bare `BMW`
  and `VOW3`). Phase 2A.3 added two explicit, narrow map entries —
  `"BMW.DE": "automaker"` and `"VWAGY": "automaker"` — as exact-string aliases for these two
  benchmark/demo symbols specifically. This is **not** general suffix stripping or ADR
  normalization (e.g. `.DE`/`.SW`/`.F` are not handled generically); it is two explicit
  dictionary entries, scoped to the symbols the manual EU Chinese EV tariffs benchmark
  actually uses. Any other missing benchmark symbol found later should get the same
  narrow, explicit treatment rather than a generic normalization rule, unless a separate
  decision is made to generalize.

---

## 6. Numeric scrubber risks

Core chain: `scrub_numeric_ranges()` (`verdict_rules.py:1317–1333`) → `_scrub_one()`
(`verdict_rules.py:594–653`), called from `_scrub_ticker_numeric_prose` (per-ticker, in
`nodes_reduce.py`) and directly inside `analysis_node` (macro-level fields).

The grammar-fix regex, `_SCRUB_VERB_BY_QUALIFIER_RE` (`verdict_rules.py` ~lines 554–557),
is:

```
r'\b(rise|increase)\s+by\s+(material|significant)\b'
```

This only cleans up "by material/significant" phrasing for the verbs **rise** and
**increase**. The percentage-range scrubber (`_SCRUB_PCT_RANGE_RE`) fires for any preceding
verb, so sentences using **surge, jump, fall, drop, plunge, spike, soar, tumble, collapse**
+ "by X–Y%" can be converted into ungrammatical phrases such as:

- "prices may surge by significant" (not fixed — "surge" isn't in the verb-fix regex)
- "demand falls by material" (not fixed — "fall"/"falls" isn't covered)
- "costs may plunge by significant" (not fixed)

No live instance of this broken phrasing was found in current test fixtures or prompts, but
the regex gap is real. **Do not fix the regex now** — list the required test matrix
(section 7, test 6) first.

The exempt-pattern list (`_SCRUB_EXEMPT_RE`) correctly protects grounded facts like
"18–36 months" (TSMC ramp) and "CoWoS" from being scrubbed. Any future grounded numeric fact
added to a prompt must be checked against this exemption list, or it will be incorrectly
scrubbed into vague language.

---

## 7. Tests required before cleanup

Items 1 and 4 below are now DONE (Phase 2A / 2A.1) — see
`tests/test_phase2a_benchmark_snapshots.py`. Items 2, 3, 5 (partially), and 6 remain
outstanding; none should be written as part of this update — they are listed here so the
next phase has a concrete checklist.

1. **DONE — Benchmark regression snapshots** for the three passing cases (semiconductor
   de-escalation, EU Chinese EV tariffs, luxury wine low-materiality dispute), asserting
   verdict, risk_score, confidence/`portfolio_net`, investor_takeaway, six-field prose
   consistency, and debug flags (`balanced_vector_calibrated`, `low_materiality_neutralized`,
   `low_materiality_rule`, `rule_results`, `low_materiality_neutralization_log`,
   `trade_calibration_log`, `archetype_bounds_log`). Implemented as
   `TestSemiconductorDeescalationSnapshot`, `TestEUChinaEVTariffSnapshot`, and
   `TestLuxuryWineLowMaterialitySnapshot` in `tests/test_phase2a_benchmark_snapshots.py`.
2. **Outstanding — Defense contractor equivalence test**: Bullish + escalation signal present
   (should stay Bullish) × Bullish + no escalation signal (should cap to Neutral), for both a
   hardcoded-ticker-list member (e.g. LMT) and an archetype-mapped-only ticker not in the
   hardcoded list, to prove the two enforcement paths are truly redundant rather than
   covering different ticker sets.
3. **Outstanding — NVDA archetype-vs-ticker-prose-guard test**: feed known-bad forbidden
   phrases ("production capacity", "manufacturing capability", etc.) through the reduce node
   and determine whether archetype Pass 1 alone catches everything
   `_apply_nvda_prose_guard`'s `_NVDA_FORBIDDEN_RE` currently catches.
4. **DONE — `balanced_vector_calibrated` consistency-validator protection test**: constructed
   a trade-policy-tariff case where a holding is calibrated to Neutral by rule T10, forced
   `consistency_validator_node`'s LLM correction path to flag it as "inconsistent," and
   asserted the calibrated verdict survives. Implemented as
   `TestBalancedVectorCalibratedConsistencyProtection` in
   `tests/test_phase2a_benchmark_snapshots.py`. This test initially failed (confirming the
   risk in section 5 was live), then was fixed to pass by the Phase 2A.1 code change in
   `nodes_consistency.py` described in section 5 — it now also serves as the regression test
   locking that fix in place.
5. **Partially covered — Prose sync test across all six fields**: the three Phase 2A
   snapshots each assert `short_term_analysis == short_term_impact` and
   `long_term_analysis == long_term_impact` for their specific holdings, but a general,
   parameterized test across every verdict-changing function in section 2 (independent of any
   one benchmark scenario) is still outstanding.
6. **Outstanding — Numeric scrub grammar test**: table-driven test, verb × qualifier matrix
   (rise, increase, surge, jump, fall, drop, plunge, spike, soar, tumble, collapse × material,
   significant, modest, substantial), asserting no output contains `" by material"`,
   `" by significant"`, `" by modest"`, or `" by substantial"` after scrubbing. Explicitly
   deferred per Phase 2A/2A.1 scope — do not add this test or fix the scrubber without a
   separate decision to do so.

---

## 8. Dangerous cleanup candidates — do not touch yet

- ~~The `balanced_vector_calibrated` protection gap in `consistency_validator_node`'s
  LLM-correction branch~~ — **fixed in Phase 2A.1** (section 5). No longer a cleanup
  candidate; it was a confirmed live bug, closed with a minimal, targeted change and locked
  in by a regression test.
- `enforce_macro_confidence_risk_caps` — final seal before END, no downstream check; any
  change here has no safety net.
- `_sync_prose_to_verdict` — called by nearly every verdict-mutating rule; any change has
  the widest blast radius in the codebase.
- The exempt-pattern list in the numeric scrubber (`_SCRUB_EXEMPT_RE`) — touching this
  without full test coverage risks either scrubbing away a grounded fact (like the TSMC
  18–36 month ramp) or leaving a new grounded fact unprotected.
- `archetypes.py`'s `_validate_registry()` module-load-time validation — runs on import; a
  mistake here breaks the entire agent at startup, not just one code path.

---

## 9. Uncertainties / verify before cleanup

- Exact line numbers throughout this document are from a read-only audit pass, not a single
  verified diff — re-grep symbol names before citing them in a PR or commit.
- `reviewer_node`'s full read/write contract was not deep-dived; it was only confirmed to
  route retries. Verify it doesn't itself mutate verdict/risk_score before relying on the
  pipeline diagram in section 1 for anything load-bearing.
- `enforce_archetype_bounds` Pass 2's prose-sync guarantee was not directly confirmed in the
  audit (unlike `enforce_defense_contractor_verdicts`, where the sync call was quoted
  directly). Verify it calls `_sync_prose_to_verdict` (or an equivalent) before treating the
  "merge defense-contractor logic into archetype bounds" direction (section 4) as
  stale-prose-safe.
- Whether the two NVDA forbidden-pattern lists (`_apply_nvda_prose_guard`'s
  `_NVDA_FORBIDDEN_RE` vs. `fabless_ai_chip_designer.forbidden_prose_patterns`) are truly
  equivalent has not been verified via direct diff — only inferred from separately-reported
  pattern lists. Diff them before assuming full overlap.
- No dedicated benchmark test files were confirmed by name for "semiconductor
  de-escalation" or "EU Chinese EV tariffs" — coverage for these two benchmarks appears to
  live inside more generically-named test files (`test_archetype_bounds.py`,
  `test_p2e_trade_policy.py`). Confirm this mapping directly before treating those
  benchmarks as covered in the regression-snapshot sense required by section 7.

---

## 10. Phase 2A.4 — demo/presentation polish

Display-only changes; no reasoning, guardrail, verdict, risk_score/confidence, P2e
calibration, `consistency_validator_node`, archetype, NVDA, defense-contractor, or numeric
scrubber logic was touched. Full backend suite unaffected: 516 passed, 0 failed
(no new backend tests were needed — these are pure formatting changes).

**Implemented:**

1. **Ticker/company name spacing** — `frontend/src/components/ResultsDisplay.tsx`'s
   `PortfolioImpactCard` now renders `{impact.ticker}` and `— {impact.name}` (literal em-dash
   in the text content, not just a CSS margin). The prior version relied solely on a Tailwind
   `ml-2` class for visual spacing, which produces no separator character when the rendered
   text is copied, scraped, or read via accessibility tools — explaining the
   "MSFTMicrosoft Corporation"-style concatenation seen in manual QA.
2. **Low-materiality grounded takeaway wording** — `_grounded_no_exposure_takeaway()` in
   `nodes_analysis.py` no longer slices the raw user query into the output sentence (which
   produced grammatically broken text like "...exposure to There is a temporary diplomatic
   dispute..."). It now uses a fixed, generic phrase ("this low-materiality event") and joins
   tickers with natural English list formatting via a new small helper,
   `_join_tickers_naturally()` ("A, B, and C" instead of "A, B, C"). The `query` parameter is
   still accepted (call-site unchanged) but intentionally unused. LLM prompt behavior
   elsewhere is untouched — this only affects the deterministic no-LLM-call fallback path.
3. **Country-detection empty-state note hidden** — `frontend/src/components/MarketSignals.tsx`
   no longer renders `signals.note` unless `macroRows.length > 0`. Since the backend
   (`nodes_signals.py`) only ever sets `note` in the "no countries detected" branch (the only
   place that string is assigned), and that branch always leaves `signals.countries` — and
   therefore `macroRows` — empty, this condition reliably hides the note in exactly the case
   the polish request targeted, without changing the backend string or wording it differently.

**Not implemented — investor-takeaway near-duplicate bullet removal:**

The request was to drop ticker-specific takeaway bullets that duplicate a "clearer final
grouped bullet" for the same ticker. This was evaluated and **not implemented** because there
is no safe, deterministic way to distinguish a true duplicate from a second bullet that
mentions the same ticker for a genuinely different reason (e.g. one bullet citing competitive
upside, another citing a separate retaliation-risk mechanism for the same holding) — removing
the "second" bullet on ticker-overlap alone risks silently deleting real, non-redundant
content. Two supporting facts:

- The deterministic coverage gap-fill logic in `_build_portfolio_takeaway()` (`nodes_analysis.py`)
  already does not add duplicate bullets for tickers the LLM already covered — this is
  directly tested by `tests/test_p2e_trade_policy.py::TestTakeawayCoverageGuard::test_already_covered_no_duplicate`.
  So the repetition observed in manual QA came from the underlying live LLM's own bullet
  generation, not from a deterministic step that could be patched at the display or
  post-processing layer without semantic judgment.
- Building that semantic judgment deterministically (without another LLM call, which was
  explicitly out of scope) would require guessing at "sameness" from surface text overlap
  alone — a heuristic that is more likely to remove legitimate content than to improve
  presentation. Per the task's own instruction ("if deduplication is risky, do not implement
  it; just report why and leave it documented"), this is deferred, not implemented.
