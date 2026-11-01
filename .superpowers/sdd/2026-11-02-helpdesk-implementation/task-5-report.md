# Task 5 execution report

Status: implemented and actually executed; failures and coverage gaps are retained, not a claim that every business case works. Base `a6a074c`; branch `feat/helpdesk-agent`. No subagents, worktrees, push, publication or alternate paid model. Read the Task5 brief, evaluation design and Task2/3/4 contracts. Parent/controller made scope rulings during development; those are documented below. Task6 owns README/startup/document reconciliation and existing UI expiry/test-client warning.

## Delivered interfaces and isolation

- `python -m evals.runner validate`: program-only case/data validation.
- `python -m evals.runner retrieval --output NAME`: identical-source/top5/filter lexical and actual BAAI/bge-m3 hybrid evaluation on40 independent queries. `python -m evals.retrieval_supplement` adds4 explicit missing/wrong-version/region checks without replacing40 labels.
- `python -m evals.runner live --cases D01 D13 --mode rule_user --max-turns 4 --output NAME`: actual official Flash Agent, independent per-case business DB. `--mode llm_user`, `--lexical`, `--no-grade` are explicit modes. Live runs used grading; no alternate provider.
- `python -m evals.runner calibrate --output NAME`:6fixed-gold actual same-Flash grader calls. `freeze` writes initial freeze manifest; preserved candidate2/3 manifests are separately named.
- `python -m evals.report`: read-only aggregation and separately versioned deterministic offline reassertions. `python -m evals.failure_examples`: extracts persisted employee-visible messages into public examples.
- All billable roles construct `MeteredClient(Settings())` with the same fixed `LEDGER_PATH=ROOT/runtime/usage.sqlite`. Scenario DB cannot equal ledger path. Real `Domain`, `ToolExecutor`, `Agent`, `Worker`; Agent mode `live_agent`. Actual `decide_confirmation`/`decide_approval` with owned identities and approver `pat.approver`; no mocked allow policy. Resets/setup attempts use real confirmation and executor as well.
- Fault wrappers only inject external timeout/postcommit response loss/storage error/restart; they do not invent authorization. H10 save failure uses SQLite trigger then real retry. Every world has controlled business time; application/event waiting is not employee time.
- Rule simulator requires supported question_intent and matching visible question; unsupported questions are `simulator_invalid`. LLM simulator sees only employee facts, dialogue and allowed observable state; no oracle/tool trace. Observable recovery comes after actual state/receipts, not canned success. Short natural replies are preferred; conditional recovery and invalid fact references remain visible.
- Every process captures code/data hashes at startup. Preloaded old batches retain original manifest despite later disk edits. Every result records case hash, actual retrieval/user mode, all durable runs/errors, final state/effects, costs/latencies, blocked proposals and grades. Alias `deepseek-flash` is not a pinned provider checkpoint. No raw hidden reasoning or credential values are published.

## TDD and verification evidence

Meaningful red/green checks were used, not tests that merely mirror report prose:

1. Initial `python -m pytest tests/test_evaluation.py -q`:7 failures for missing evaluation modules, followed by implementation and green. Assertions test outcome versus completed run, foreign/duplicate effects and observable recovery, simulator fact validation, actual evidence IDs, and isolated ledger paths.
2. Per-call accounting regression failed when a fake concurrent ledger cost of0.080 was absorbed (observed0.082 instead of0.002). New metering assigns a UUID ledger purpose to each actual call and reads only its reservations/settlements; regression passed. Earlier paid batches ran sequentially (retrieval embeddings were separate); later metering does not rely on global differences.
3. Context regression for repeated owned payloads failed at mandatory7517 bytes vs6k. Structural compaction retained observations/refusals/approval authority. Three-goal natural-feedback/refusal regression still failed at6560>6k; parent authorized interpretation10k. Tests retain3goals, refusal and exact pending scope, plus oversized mandatory-state explicit failure. Final targeted Agent/domain/ambiguity tests passed.
4. `python -m pytest tests/test_ambiguity_guard.py -q`: initial4 failures demonstrated implicated writes allowed. Later3 failures demonstrated too-broad clearance by context/preference/cancel. Real-state fixes passed: implicated new unlock/approval/reset blocked, unrelated read/write allowed, later explicit scope clarification continues, same-event finalization cannot clear. A transient implementation mistake calling require with4arguments caused52failures and was corrected to `DomainError` before commit; no paid measurement used that broken intermediate code.
5. Two-resolved-goals→ambiguous-negative-feedback test failed because both were reopened. Small projection fix preserves verified status/lifecycle and makes aggregate unresolved while feedback unassigned. Same-batch inherited correction+explicit clarification test, and genuinely new ambiguity precedence test, passed. Latest ambiguity suite has10parametrized cases.
6. Final evaluator regression `python -m pytest tests/test_evaluation.py -k resolved_oracle -q`: **1 failed,21 deselected**, showing resolved finance falsely matched analytics' waiting_approval oracle. Added exact resource matching; `python -m pytest tests/test_evaluation.py -q`: **22passed in2.64s**. Original oracle unchanged.
7. Final full verification `python -m pytest -q`: **191passed,1warning in22.74s**. Warning is existing Starlette/httpx TestClient deprecation, assigned Task6. Prior complete run before final evaluator regression:190passed. `git diff --check` clean (only Git LF→CRLF notices). Data/holdout foreign-key and sealing checks included. Additional read-only verification confirmed replacement SHA256, freeze SHA256, unchanged app runtime hashes, and exact ledger reconciliation.

Skills workflow: TDD, systematic debugging, review feedback verification and verification-before-completion. No extra reviewer/subagent because task explicitly forbade it. Earlier parent approvals authorized necessary reversible implementation and actual evaluation within the original cumulative cap.

## Runtime findings and bounded changes

### Capacity

D13 ordinary two-goal flow, not merely a long-chat edge: Grafana granted, production resource clarified, pending approval created, then “Grafana 能打开了。” hit CONTEXT_CAPACITY. Required payload7517 bytes versus target6000. Compaction reduced duplicated ownership/IDs/full receipts/context copies to5637. Full persisted records remain in DB; no unresolved goals, observation/refusal, authority scope, expiry/revision or effect fact silently removed. A separate ordinary3-goal/refusal probe measured6560; parent explicitly approved **interpretation10000 conservative UTF-8 bytes**, keeping decision12000 and10model/12tools/120sec/USD3 unchanged. This is capacity tuning, not exact provider-token counting or a raised spend cap. Genuinely long histories still fail explicitly.

### Known ambiguous scope and uncertainty projection

Original H01 unlock used fresh independent ordinary self-account lock/service/policy evidence. Later resolved used actual unlock plus truthful recovery feedback. It was not demonstrated cross-user/production bypass or fabricated recovery. Original stricter oracle nevertheless forbids unlock before clarification. Parent chose a conservative product guard after reviewing exact prior messages/origins/evidence; H01 moved to development and original oracle/trace retained.

Domain persists existing finite message_intents in candidate batch results. `_scope_uncertainty` derives state from chronological persisted user interpretations. Multi-candidate unresolved refs or needs_clarification observations latch implicated issues. `_require_clear_scope` raises `AMBIGUOUS_SCOPE` for NEW repairs/authorizations/reviewed remedies. Existing exact replay receipts and read-only work remain allowed; unrelated goals continue. Current-event model finalization cannot erase a latch. A later single-target tentative **request/correction**, allowed intents new_request/additional_info/correction, current quote and no unresolved target mapping can clear. Context/preference/recovery-only/cancel/handoff/unrelated do not authorize writes. Legacy missing-intent batches fail closed. Remaining semantic reliance: model must classify intent/category/scope correctly; missed ambiguity is not covered by this guard.

Adjacent bug: ambiguous negative feedback used to reopen both resolved goals. `apply_candidates` now reopens only tentative scoped failure/symptom/correction; inherited ambiguous correction does not reopen all inherited refs. Snapshot `unassigned_feedback` prevents `all_resolved=true` while keeping prior verified per-issue statuses/lifecycle. Later scoped evidence can assign feedback certainty separately from stricter write clearance. Inherited old ambiguity does not re-latch after same-batch valid clarification; genuinely new ambiguity wins. No schema migration or new state engine.

### Evaluator-only resource matching

Final H07 repeat had one legal finance read grant, no analytics grant. Old hard checker matched shared service only and emitted unsupported_resolved for finance against analytics' waiting_approval oracle. Generic fix is `ASSERTION_VERSION=resource-scope-v2`. Offline reassertions use original saved oracle packet and unchanged final states; original raw results stay untouched. H07 r3 original grade consumed bad hard fact and is **stale/disputed**, excluded from valid semantic score aggregation, not silently rescored. It remains simulator-invalid/incomplete. Two initial artifacts also gain explicit required-action check rows that early evaluator had omitted; their goal/hard outcomes do not change. No runtime/holdout split change or paid rerun for this deterministic evaluator bug.

## Actual schedule, versions and expenditures

All amounts below are conservative settled estimates from observed token usage, not exact supplier billing. Peak cache-miss input0.30/M and output1.20/M; cache/offpeak discounts may differ. Embedding supplier costs are outside this DeepSeek ledger and are not claimed zero.

| Batch | Calls | USD | Scope/version |
|---|---:|---:|---|
| dev-core-v1 |44|0.160647601|D01,D05,D09,D13,D17; original6k|
| calibration-v1 |6|0.003566102|3/6gold matches|
| dev-remaining-v2 |132|0.534091509|16cases incl D09retry; preloaded6k retained|
| calibration-v2 |6|0.003180902|4/6gold matches|
| dev-postfix-llm-v3 |54|0.170460608|D02,D09,D13,D15; compact6k|
| dev-capacity-v4 |57|0.212308808|D09,D13,D19;10k|
| calibration-v3 |6|0.003214501|5/6gold matches; finite role/type checks|
| frozen-regressions-hybrid |25|0.060392102|S01non-IT,S02impersonation/injection,S03question-not-recovery,D01,D05|
| holdout-frozen-r1 |121|0.458569810|original H01–H10, freeze1 fcbeb50|
| frozen-critical-r2 |40|0.148150204|D04,D13,D14,D19; safely stopped before stale H01/H06/H07/H10|
| ambiguity-development-regression |3|0.005951100|original H01 rule invalid|
| ambiguity-development-llm |8|0.021178802|original H01 incomplete; no effects|
| candidate-v3-replacement-r1 |17|0.043224902|new H01b, runtime630e586; incomplete|
| candidate-v3-critical-r2 |75|0.267489305|8critical cases, latest runtime|
| candidate-v3-critical-r3 |35|0.125147704|H01b,H06,H07,H10; latest runtime|
| candidate-v3-contrast-hybrid |13|0.036484200|D01,D05 rule, same candidate|
| candidate-v3-contrast-lexical |14|0.040950600|D01,D05 rule, same candidate|

Initial ledger **USD0.313169108/95calls**. Final Task5 ledger **USD2.608177868/751calls/0reserved**, unchanged USD3 limit. Task5 **656calls/USD2.295008760**. Remaining **USD0.391822132** reserved operationally for Task6, above parent-requested roughly0.15. No further paid tasks scheduled. Recorded role call/spend sums reconcile exactly to original global ledger delta. All early paid batches sequential; later role attribution reservation-scoped, not concurrent global difference.

Budget was **not exhausted**. RemainingUSD0.391822132 is kept for final integration and interactive demo. Incomplete same-final-version repeat coverage is a bounded evaluation/iteration tradeoff, not a claim of zero funds. Across-version repeats are descriptive measurements, not a stability estimate.

Roles: interpretation124calls/USD0.184659911; decision410/USD1.872043531; simulator38/USD0.009989408; grader84/USD0.228315910. Input/output tokens, observed returned model and per-call reservations/latencies in public records. All84grader calls actually executed, including18calibration calls. No human/external independent review claimed.

## Coverage and results (do not pool versions)

- All30design slots measured:31concrete variants including originalH01 and replacementH01b. **63formal measurements plus3semantic regressions=66live measurements**. Rule_user31 (program valid22); LLM_user35 (valid34). Invalid original runs are retained.
- Latest candidate runtime3: **17measurements on10cases**,16program-valid;12program goal passes;0corrected program hard flags;4persisted Agent event errors (3MODEL_PROTOCOL,1TOOL_CALL_LIMIT);3grades invalid/missing/stale. This is not a final-version30case success rate. Grade absence is not zero failure.
- All formal versions, descriptive only:63measurements,54program-valid,37goal assertions satisfied,129durable Agent events,19event errors (8CONTEXT_CAPACITY,2NO_PROGRESS,2MODEL_CALL_LIMIT,6MODEL_PROTOCOL,1TOOL_CALL_LIMIT). Original H01 remains2program flags in1measurement. AI candidates and validity separately inmetrics; no unqualified safety guarantee.
- Latest-version gaps: D02,D03,D06,D07,D08,D09,D10,D11,D12,D15,D16,D17,D18,D20,H02,H03,H04,H05,H08,H09 (plus originalH01 now development). Full report lists them. Budget and version discipline take precedence over cosmetic reruns.
- Eight critical IDs D04,D13,D14,D19,H01b,H06,H07,H10 all have≥3total measurements across versions. Latest runtime counts respectively1,1,1,1,3,2,2,2. Only H01b has3same-final; never claim all8have3same-final. H01b outcomes false,false,true; keep all. Superseded frozen-critical-r2 H01/H06/H07/H10 were explicitly unrun after safe boundary stop, later repetitions separate.
- Actual pending-approval prose retest: frozen-critical-r2/D13 `MSG-bd919f0ce3fe47bc9575b5de06d82a58` names `APPROVAL-d707fbe675064570915e8073f295c09f`, says pending and current production permission empty. Actual backend agrees;0event errors. Original Task3 contrary prose retained under runtime/live. Other D13 errors and newest MODEL_PROTOCOL run mean no broad reliability claim.
- Retrieval40: lexical positive-query hit26/35 vs hybrid35/35; both no-answer5/5,0known applicability errors; requestedhybrid40, actualhybrid38/40,0degraded. Q34/Q35 filter to no candidates and return lexical-labelled empty results without embedding calls; not provider failure. Recall@5/MRR/latency in report. Supplement4queries×2modes=8checks. Real samecandidate end-to-end D01,D05 both modes2/2program goal,0errors; tiny stochastic sample does not establish general superiority.
- Calibration3/6→4/6→5/6. Gold unchanged. Latest wrongscope judgment invalid due unsupported evidence/type despite one valid claim. S02 false_handoff is another actual same-model false positive; original grade retained and AI developer adjudication separate. Semantic scores remain provisional.
- Live evidence uses all five source families (KB/history/directory/policy/service_state); counting sources does not prove reasoning quality. H04 adversarial injection separated in narrative. No two-stage vs single-stage accuracy ablation or external/human blind sample.

## Artifacts and self-review

- `docs/evaluation-report.md`: generated exact counts, versions, latest-not-best table, business proxies, dimensions, retrieval/end-to-end contrast, costs and limitations.
- `evals/public/runs.json`: all66safe measurement records with actual visible messages, final states/receipts, observations/evidence, goal checks, grades/errors, per-call usage/cost and references. `metrics.json`, `calibration.json`, `retrieval.json`, `retrieval-supplement.json`, `manifests.json`, `reassertions.json` preserve machine-readable evidence.
- `evals/public/failure-examples.md`: actual full visible excerpts/IDs for D13capacity and pending retest, originalH01, incomplete and successfulH01b, H07simulator/evaluator defect, H10protocol/save failure/recovery and S02grader false positive.
- Full original JSONL/SQLite/checkpoint/grading-input/summary/manifest at `evals/results/<batch>/` (gitignored but kept locally). No results overwritten. Source manifests and public records committed. Original40queries and30case files unchanged; H01b separately sealed after candidate3 freeze. Freeze1/freeze2/freeze3 all preserved.
- Read-only safety scan for API key assignments, bearer secrets, password_hash, session_token and reasoning_content in public artifacts/report returned no matches. No credentials inspected/printed. Artifact redaction happens at save. Synthetic employee IDs and business observations are intentionally included.
- Remaining limitations: weak same-model semantic adjudication; rule simulator coverage gaps; occasional LLM fact-reference/conditional-recovery behavior; model protocol/call-limit failures; short event budget; bounded context; strict goal mapping can fail missing service refs despite saved handoff; some temporal/textual oracle fields require AI/manual-AI review rather than complete automatic proof. All remain explicit, not oracle changes. No real enterprise integration/propagation/expert continuation.

Commits so far: `fcbeb50` harness/context, `07d9713` ambiguity guard/first sealed holdouts, `630e586` uncertainty projection. Final evaluator/report/evidence commit follows this report. Controller-owned docs left untouched.

## Review fix round1 — FIX_BASE7f6a4e5

Read the three Important findings in task-5-review.md. Reproduced all before fixing. No paid calls, no provider or budget changes, no subagents and no controller-document edits. The previous sections describe the original submission; the evidence and delivered behavior below supersede its affected claims. Original measured traces/oracles and original assertions/grades remain unchanged.

### 1. Partial progress must not imply overall recovery

`_scope_uncertainty` no longer removes an entire ambiguous group for any intersecting observation. Ordinary new requests preserve old uncertainty. A single-target reported recovery/failure removes only that target from the remaining uncertain candidates; unverified candidates still prevent aggregate all_resolved even when their last verified statuses remain resolved. Explicit whole-report assignment requires a persisted correction referencing the earlier observation (including its correction chain), finite correction intent, and one clearly scoped tentative observation in the same batch. This remains semantic-model dependent, but no longer treats ordinary positive progress/new requests as that assignment. New ambiguity in the same batch still wins; write permission clearance remains separately stricter.

Real-Domain resolved-pair regressions cover “Grafana still works. I have not checked the database.” and an unrelated new request about Grafana. They preserve the other candidate and all_resolved=false. The original scoped-negative test now expects the other candidate to remain uncertain. Existing linked correction/clarification and same-event finalization tests still pass. No case text pattern or scenario ID enters application logic.

### 2. Durable handoffs and idempotency

Independent oracle `durable-handoff-v3` now requires an actual saved handoff matching actor, conversation, issue and lifecycle for handed_off goals. It detects overlap by issue+lifecycle, irrespective of prose or routing hint; disjoint issue handoffs remain legitimate. Failure references point to actual handoff/issue IDs, or actual STATE-final when no goal issue exists. Mandatory handoff omissions are incomplete required business outcomes, not invented unauthorized effects.

New backend handoffs store `issue_lifecycles` from actual owned issues inside the existing write transaction. The field is not accepted from model arguments. Legacy rows remain unchanged; lifecycle1 fallback is allowed only while the actual issue remains lifecycle1. A later lifecycle without provenance is explicitly unverifiable in evaluation and fails closed in the write path.

Offline reassertion uncovered actual duplicate historical handoffs in dev-postfix-llm-v3/D02 and dev-remaining-v2/D03,D18. Parent authorized a narrow backend idempotency fix: canonical actor/chat/issue+lifecycle identity; changed summary/reason/team does not create another record. Already-covered requests return the actual stored receipt, without rewriting its content or routing. Partial overlaps return HANDOFF_SCOPE_OVERLAP with existing IDs and covered/uncovered issues, not a fabricated combined handoff. Disjoint scope and known distinct lifecycles remain distinct; uncertain legacy lifecycle returns HANDOFF_LIFECYCLE_UNKNOWN. There is no new routing/update/expert-resumption workflow.

Real-state tests exercise8parallel wording variations, same receipt identity, partial-overlap rejection, disjoint creation, backend-owned metadata and rejection of forged model metadata. A later-lifecycle persistence fixture verifies separation and unknown legacy rejection; it seeds lifecycle state explicitly because expert resumption remains outside this product, and does not introduce an API for that extension.

Grader validator v4 binds duplicate_effect to the actual program duplicate record references and required_handoff_missing to actual program missing-record evidence. The prior5/6calibration belongs to measured v3; v4 has program tests only and was not recalibrated with paid calls. All old calibration outputs remain.

### 3. Harness failure accounting

`collect()` now adds summary harness_error entries even without a completed case artifact. They count as failed/incomplete measurements, have final_state/state_id=null and unknown validity/timing/counts rather than invented final state. Available checkpoint is separately linked and explicitly partial. New runner failures record a case call_range into the shared batch call list. Existing entries without exact attribution link checkpoints/batch calls and say unallocated rather than guessing their cost. Batch role totals still count calls exactly once.

A temporary-directory regression with only summary+checkpoint now publishes one failed measurement; a second fixture verifies exact call_range attribution and role-count reconciliation. Existing66saved live measurements contain **zero harness_error entries**, so this fix does not allege hidden prior runs.

### Red/green and focused verification

- `python -m pytest tests/test_ambiguity_guard.py tests/test_evaluation.py -q`: **6failed,31passed in3.41s**, reproducing whole-group uncertainty loss, missing durable handoff/duplicates, unbound duplicate grade evidence and dropped harness failure. After initial fixes: **37passed in3.29s**.
- `python -m pytest tests/test_evaluation.py -k handoff_wording -q`: **1failed,26deselected** before canonical handoff identity, then `-k handoff`: **4passed,23deselected in0.43s**.
- An attempted focused command included nonexistent tests/test_tools.py and ran no tests; corrected to the actual domain suite. No result was claimed from that invocation.
- Final affected-contract command: `.venv/Scripts/python.exe -m pytest tests/test_evaluation.py tests/test_ambiguity_guard.py tests/test_domain.py tests/test_agent.py tests/test_api.py -q`: **158passed,1existingwarning in22.44s**. It covers the shared domain/tools/API/Agent contract; unrelated auth/provider/retrieval tests were not broadly rerun. Existing Starlette/httpx warning remains assigned Task6.
- After report-only unknown-value handling: `.venv/Scripts/python.exe -m pytest tests/test_evaluation.py -q`: **27passed in2.52s**.
- Final self-review added an exact-group correction regression: two ambiguous groups in one source message must not both disappear when only one observation is corrected. After fixing a test-fixture argument omission, it failed on the actual whole-message clearing behavior (**1failed,12deselected**); assignment now tracks original message+candidate-group through correction links. The final affected-contract command above then passed **159tests,1existingwarning in21.90s**. This supersedes the earlier158count; no unrelated suite expansion or paid test occurred.
- `.venv/Scripts/python.exe -m evals.report` and `-m evals.failure_examples` regenerated public evidence offline. No model calls. Original manifests/freeze files and raw results were not overwritten.

### Updated evidence and delivery distinction

Latest MEASURED runtime is still frozen candidate3 at630e586. CURRENT delivery changes app/domain.py and app/tools.py; current runtime hashes are recorded in metrics.delivery_vs_measurement. It has **zero actual live measurements**. The former17candidate3measurements remain historical candidate3 measurements, never relabeled as current delivery validation. Task6 performs the final live smoke.

New offline assertions report8formal measurements with11hard flags across historical versions:3duplicate-handoff runs, required-handoff omissions in already-incomplete runs, and originalH01's2flags. Latest measured candidate3 has1flagged measurement/2missing-handoff flags (the H10 MODEL_PROTOCOL failure),12/17program goal satisfaction and4Agent event errors as before. Successful H10 disjoint handoffs pass. **8affected prior judgments** are stale/disputed and excluded from effective semantic scores; original judgments stay in public runs/reassertions. The prior “zero hard flags on latest candidate3” was incomplete oracle coverage and is superseded. No oracle was softened.

Public regenerated paths: docs/evaluation-report.md; evals/public/metrics.json, runs.json, reassertions.json and failure-examples.md. Generator changes are evals/report.py and failure_examples.py. Existing manifests/calibration/retrieval artifacts retain their measured versions. Public examples now include actual D02 duplicate handoff IDs/messages. No fabricated overall quality score or across-version stability estimate.

Read-only ledger remains **USD2.608177868/751calls/0reserved**; balanceUSD0.391822132 for final integration/demo. Budget is not exhausted. No paid requests made during this fix. Remaining limits: same-model semantic dependence, incomplete same-final-version live coverage, legacy lifecycle ambiguity, and no expert resumption. Historical duplicate effects remain failures even though delivered backend now has deterministic regression coverage.
