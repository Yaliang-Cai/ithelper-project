# Helpdesk Agent Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task by task. Follow the task brief, preserve the contracts below, and report verification evidence. Do not publish or push.

**Goal:** Deliver the locally runnable, model-driven employee IT agent described in the reviewed design, with real mock-system changes and reproducible evaluation.

**Architecture:** A single business Agent has interpretation and decision stages. Backend state, policy checks and persistence enforce permissions and outcome prerequisites. Retrieval combines lexical matching and SiliconFlow embeddings, with explicit lexical degradation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLite, HTTPX, Argon2, NumPy, BM25S/Jieba, plain HTML/CSS/JS, pytest and Playwright.

**Spec:** docs/01-system-design.md, docs/02-evaluation.md, docs/03-defense-and-extensions.md, docs/04-prompts-and-contracts.md.

## Global Constraints

- All project LLM calls, including user simulation and grading, use the official DeepSeek Flash model ID `deepseek-flash`. No paid model fallback.
- Embeddings use `BAAI/bge-m3` at `https://api.siliconflow.cn/v1`; never silently select `Pro/BAAI/bge-m3`. Missing key means explicitly degraded lexical retrieval, not simulated vectors.
- User-authorized cumulative project DeepSeek budget is USD 3. Persist spending across processes/restarts, reserve before calls, and retain conservative charges for uncertain requests.
- Resetting the demo or creating an evaluation world must never reset or relocate the shared project spending ledger. Scenario databases are separate from the one cumulative budget database.
- Keys are loaded from the gitignored .env or environment, never committed, printed, placed in model context or returned to the browser.
- Single business Agent; no fixed per-scenario response branches. Test oracles never enter the application runtime.
- Multiple issues retain independent outcomes. Claims of approval, successful execution, or handoff do not create those facts.
- Approval and confirmation APIs enforce identity, exact scope, expiry and cancellation. Writes are idempotent and verified.
- Five scenario families and all five data source types remain in scope. Cross-chat personal memory, full propagation simulation, expert resumption, and real enterprise integrations remain extensions.
- Every evaluation result records actual mode: fixture, live Agent, rule user, LLM user, AI grader, lexical/hybrid. No invented scores or human evaluation claims.
- Work is in the dedicated new repository D:/药明-test on feat/helpdesk-agent. No existing application or main branch is being modified. No publishing is authorized.

## Task 1: Provider configuration, budget and connectivity

**Files:** app/__init__.py, app/config.py, app/providers.py, app/budget.py, scripts/check_connections.py, tests/test_providers.py, pyproject.toml, .env.example.

**Interfaces:** Settings.load(env_file='.env') loads secrets and defaults; Settings exposes deepseek_model, deepseek_base_url, embedding_model, embedding_base_url, llm_budget_usd and runtime_dir. DeepSeekClient(settings, budget, transport=None).complete(messages, *, purpose, tools=None, response_format=None, thinking=False, max_tokens=2048, tool_choice=None) returns the validated provider response dictionary including usage and message protocol fields. EmbeddingClient(settings, transport=None).embed(texts) returns list[list[float]]. BudgetLedger(path, limit_usd) reserves, settles and reports cumulative costs atomically. API methods may be synchronous initially; the later worker executes them outside request handling.

- [ ] Create tests that fail before production code: forbidden model fallback, invalid/empty embedding results, secret-safe exceptions, disabled embedding without key, complete reasoning/tool message preservation, successful usage accounting, timeout reservation retention, concurrent reservations unable to exceed budget, and restart persistence.
- [ ] Use actual temporary SQLite ledgers and HTTPX MockTransport responses. Example behavioral requirement:

```python
def test_budget_survives_restart(tmp_path):
    from app.budget import BudgetLedger, BudgetExceeded
    ledger = BudgetLedger(tmp_path / 'usage.sqlite', limit_usd=0.01)
    reservation = ledger.reserve(0.009, purpose='agent')
    ledger.settle(reservation, 0.008)
    reopened = BudgetLedger(tmp_path / 'usage.sqlite', limit_usd=0.01)
    import pytest
    with pytest.raises(BudgetExceeded):
        reopened.reserve(0.003, purpose='grader')
```

- [ ] Implement validated configuration, persistent reservations and HTTP adapters. Use bounded HTTP timeouts, no silent network retry; caller owns request budgets. Peak Flash pricing for conservative estimates: USD 0.30 / million input tokens, USD 1.20 / million output tokens, checked 2026-11-02. Cache savings may be reflected in actual accounting but not assumed for reservations. Missing usage retains reserved cost. Invalid provider responses do not expose request headers or raw exception dumps.
- [ ] Add a CLI with masked readiness output, model-list authentication, small JSON completion, native tool-call round trip, and embedding probe only when its independent key exists. Its calls use the same ledger as the application. Save only safe metadata to runtime/connectivity.json.
- [ ] Install into .venv, run focused tests, run the bounded live connectivity check, self-review and commit explicit files. Do not read or print secrets as evidence.

## Task 2: Synthetic world, state and policy-controlled actions

**Files:** app/storage.py, app/auth.py, app/domain.py, app/tools.py, app/seed.py, data/world.json, data/kb/*.md, data/history.json, tests/test_domain.py, tests/test_auth.py.

**Consumes:** Settings and runtime directory from Task 1. **Produces:** Store(db_path), initialize/reset_demo; authenticated employee/session services; issue/message/evidence persistence; ToolExecutor.call(name, arguments, actor_id, conversation_id) returning the design envelope; approval and confirmation decision services; queued events.

- [ ] Write failing tests for cross-user access, unsupported writes, invalid/expired approval, refusal, repeated approval/action, unknown execution after commit, independent issue outcomes, failed handoff persistence and restart recovery.
- [ ] Build deterministic shared IDs and five rich source types. Seed six roles and eight services, current and outdated KB versions, current/mismatched outages, policy prohibitions and causal counterexamples. No oracle or scenario-ID routing in the runtime.
- [ ] Implement transactional state changes, backend-generated provenance, action fingerprints, persistent operations and receipts, exact scope checks and password hashing. Complete tool inventory follows system design section 8; query tool schemas do not accept actor overrides.
- [ ] Run focused state/security tests, self-review and commit. Report the exact public interfaces used by Task 3 and Task 4 before integration.

## Task 3: Retrieval, context and model-driven Agent

**Files:** app/retrieval.py, app/context.py, app/contracts.py, app/prompts.py, app/agent.py, tests/test_retrieval.py, tests/test_agent.py.

**Consumes:** Task 1 providers, Task 2 state/tools. **Produces:** Retrieval.search(query, filters), ContextBuilder.build(stage, actor_id, conversation_id, event), Agent.handle(event_id), structured turn/state proposals.

- [ ] Write meaningful fixtures for ambiguous feedback, claimed approval, two goals, relation reversal, missing evidence, protocol pairing, truncation that preserves constraints, and stop budgets.
- [ ] Implement keyword and embedding retrieval with versioned cache and rank fusion; expose degraded status. Directory/status/policy queries stay structured.
- [ ] Implement the documented interpretation contract and atomic validation; store raw messages first. Build fresh decision context from persisted facts and retain native provider fields within a run.
- [ ] Implement tool selection and submit_turn finalization, bounded repairs, two distinct remedy paths, evidence-based acceptance and honest fallback responses. Current-chat history only. No tool generated by a fixture in live mode.
- [ ] Run focused tests and small live conversations, record actual context/tool traces with no secrets or raw internal reasoning displayed, self-review and commit.

## Task 4: Web app, approval worker and user experience

**Files:** app/main.py, app/worker.py, app/ui/index.html, app/ui/app.js, app/ui/styles.css, tests/test_api.py, scripts/run_local.ps1.

**Consumes:** authentication, persistence, Agent and queue. **Produces:** local employee chat, approver page, protected debug view and durable event processing.

- [ ] Write API integration tests for login/session regeneration/logout, CSRF, forbidden roles, message ownership, confirmation decisions, scope-bound approval, duplicate events and cancellation races.
- [ ] Implement the API table in design section 15, asynchronous runs with polling, durable queued events and startup recovery. An approval resumes only its original issue; logging out is not automatic cancellation.
- [ ] Build a small accessible chat with independent issue status, source references, confirmation cards, approval/handoff identifiers, explicit mock-system labels and role-specific pages. Render untrusted text safely.
- [ ] Verify real browser flows, role switching, mobile usability and errors; commit after relevant API tests pass.

## Task 5: Evaluation, data isolation and results

**Files:** evals/cases/*.json, evals/runner.py, evals/simulators.py, evals/grader.py, evals/retrieval_queries.json, tests/test_evaluation.py, docs/evaluation-report.md.

**Consumes:** same application services and model provider, independent scenario databases. **Produces:** actual reproducible traces, hard assertions, dimension scores and failure analysis.

- [ ] Encode D01–D20 development and H01–H10 held-out blueprints with world/user-card/oracle/fault-plan visibility separation. Grade on real state and receipts, never actor self-report.
- [ ] Implement rule and LLM users with short natural messages and known-fact checks. Confirmation and approval drivers use actual authorization APIs. Report unsupported simulator steps as invalid instead of forcing canned success.
- [ ] Implement five 0/1/2/N/A rubric dimensions and hard-failure gates, costs by role, latency and simulator validity. Calibrate the grader on known good/bad traces. Same Flash model throughout.
- [ ] Run program checks, approximately forty retrieval queries, BM25/hybrid contrast when embeddings are available, then live cases and repeated critical cases within the cumulative budget. Keep incomplete/environment-blocked cases visible; never relabel them as passed.
- [ ] Save reports with versions and failures, self-review and commit. Holdout failures used for tuning become development cases and require new held-out variants.

## Task 6: Integration, delivery and final review

**Files:** README.md, .env.example, docs/implementation-status.md, scripts/demo_reset.py, relevant corrected modules and regressions.

- [ ] Confirm a clean local setup and restart through README, run all program tests, inspect fresh-input browser conversations and reset behavior.
- [ ] Reconcile implemented behavior against all four design documents; annotate deviations rather than silently rewrite agreed goals. Keep design material separate from measured results.
- [ ] Request independent code review of the full change. Fix critical findings with regressions and a scoped re-review.
- [ ] Deliver startup commands, demo users, actual evaluation summary, known limitations and credential/budget readiness. Keep the feature branch locally; no push, publication or external messaging.
