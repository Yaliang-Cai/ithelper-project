"""Offline regressions for final-review shared-budget and local-host boundaries."""
from pathlib import Path
import json
import pytest
from app.config import Settings
from app.budget import BudgetLedger
from app.providers import ProviderError


def test_connectivity_uses_project_config_and_ledger_despite_cwd_and_runtime(tmp_path, monkeypatch):
    import scripts.check_connections as probe
    loaded = []; selected = []
    custom = tmp_path / 'business-output'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe.Settings, 'load', lambda *args: loaded.append(args) or Settings(runtime_dir=custom))
    class StopBeforeProvider(Exception): pass
    def intercept(path, limit):
        selected.append(Path(path)); raise StopBeforeProvider
    monkeypatch.setattr(probe, 'BudgetLedger', intercept)
    with pytest.raises(StopBeforeProvider): probe.main()
    root = Path(probe.__file__).resolve().parents[1]
    assert loaded == [(root / '.env',)]
    assert selected == [root / 'runtime/usage.sqlite']


def test_exhausted_canonical_connectivity_budget_makes_no_provider_requests(tmp_path, monkeypatch):
    import scripts.check_connections as probe
    import app.providers as providers
    canonical = tmp_path / 'canonical/usage.sqlite'
    ledger = BudgetLedger(canonical)
    limit = Settings().llm_budget_usd
    ledger.settle(ledger.reserve(limit, purpose='prior-project-requests'), limit)
    before = ledger.report(); requests = []
    monkeypatch.setattr(probe, 'PROJECT_LEDGER', canonical, raising=False)
    custom = tmp_path / 'alternate-runtime'
    monkeypatch.setattr(probe.Settings, 'load', lambda *args: Settings(runtime_dir=custom, deepseek_api_key='fixture-key'))
    def blocked_request(*args, **kwargs):
        requests.append(True); raise ProviderError(category='offline')
    monkeypatch.setattr(providers, '_request', blocked_request)
    probe.main()
    assert not requests
    assert ledger.report() == before
    assert not (custom / 'usage.sqlite').exists()
    assert json.loads((custom / 'connectivity.json').read_text())['json_completion'] == 'budget_exhausted'


def test_future_evaluation_batch_cannot_rewrite_task5_end_snapshot(tmp_path, monkeypatch):
    import evals.report as report
    (tmp_path / 'evals/results/task5-fixture').mkdir(parents=True)
    (tmp_path / 'evals/freeze.json').write_text(json.dumps({'hashes': {}, 'git_head': 'fixture'}))
    (tmp_path / 'evals/runner.py').write_text('# fixture')
    calls = [dict(purpose='agent_decision', estimated_spend_usd=2.295008760 if i == 0 else 0, reservation_delta_usd=0, latency_seconds=0) for i in range(656)]
    (tmp_path / 'evals/results/task5-fixture/summary.json').write_text(json.dumps({'calls': calls}))
    monkeypatch.setattr(report, 'ROOT', tmp_path)
    _, roles, _, _ = report.collect()
    before = report.cost_scopes(roles)
    later = tmp_path / 'evals/results/later-fresh-batch'; later.mkdir()
    (later / 'summary.json').write_text(json.dumps({'calls': [dict(purpose='grader', estimated_spend_usd=.03, reservation_delta_usd=.01, latency_seconds=0)]}))
    _, roles, _, _ = report.collect()
    after = report.cost_scopes(roles)
    assert before['task5'] == after['task5']
    assert after['task5']['end_ledger']['spent_usd'] == 2.608177868
    assert after['task5']['request_delta'] == 656
    assert after['task5']['settled_estimate_delta_usd'] == 2.295008760
    assert after['subsequent_evaluations'] == {'calls': 1, 'settled_estimate_usd': .03, 'unsettled_reservations_usd': .01}
