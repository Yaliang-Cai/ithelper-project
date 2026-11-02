from app.budget import BudgetLedger
from app.domain import Domain
from app.storage import Store


def test_demo_reset_reseeds_business_and_preserves_cumulative_ledger(tmp_path):
    from scripts.demo_reset import reset
    ledger = BudgetLedger(tmp_path / 'usage.sqlite')
    reservation = ledger.reserve(.1, purpose='unsettled-request')
    ledger.settle(reservation, .03)
    ledger.reserve(.02, purpose='uncertain-request')
    d = Domain(Store(tmp_path / 'business.sqlite').initialize())
    d.create_conversation('alice.sales', 'Reset this demo')
    before = ledger.report()
    result = reset(tmp_path)
    assert result['business_reset'] and result['spending_ledger_unchanged']
    assert not d.list_conversations('alice.sales')
    assert BudgetLedger(tmp_path / 'usage.sqlite').report() == before


def test_evaluation_cost_scope_does_not_absorb_later_integration_calls():
    from evals.report import measurement_costs
    roles = {'interpretation': {'calls': 10, 'estimated_spend_usd': .1, 'uncertain_reserved_usd': .02}, 'grader': {'calls': 3, 'estimated_spend_usd': .03, 'uncertain_reserved_usd': 0}}
    result = measurement_costs(roles)
    assert result == {'calls': 13, 'settled_estimate_usd': .13, 'unsettled_reservations_usd': .02}


def test_evaluation_counts_69_reserved_requests_separately_from_70_attempts(tmp_path, monkeypatch):
    import json
    import evals.report as report
    results = tmp_path / 'evals/results'
    historical = results / 'task5-fixture'
    later = results / 'delivery-iteration-v6-targeted'
    historical.mkdir(parents=True)
    later.mkdir()
    (tmp_path / 'evals/freeze.json').write_text(json.dumps({'hashes': {}, 'git_head': 'fixture'}))
    (tmp_path / 'evals/runner.py').write_text('# fixture')
    # Older saved calls predate reservation IDs; preserve their reviewed count.
    legacy = [dict(purpose='agent_decision', estimated_spend_usd=2.295008760 if i == 0 else 0,
                   reservation_delta_usd=0, latency_seconds=0) for i in range(656)]
    (historical / 'summary.json').write_text(json.dumps({'calls': legacy}))
    paid = [dict(id=f'CALL-{i}', purpose='agent_decision', ledger_reservation_ids=[f'reservation-{i}'],
                 estimated_spend_usd=.001, reservation_delta_usd=0, latency_seconds=.1,
                 usage={'prompt_tokens': 100, 'completion_tokens': 10}) for i in range(69)]
    blocked = dict(id='CALL-blocked', purpose='agent_decision', error='BudgetExceeded',
                   ledger_reservation_ids=[], estimated_spend_usd=0, reservation_delta_usd=0,
                   latency_seconds=.001)
    (later / 'summary.json').write_text(json.dumps({'calls': [*paid, blocked]}))
    monkeypatch.setattr(report, 'ROOT', tmp_path)

    _, roles, _, _ = report.collect()
    assert roles['agent_decision']['calls'] == 725
    assert roles['agent_decision']['attempted_calls'] == 726
    assert roles['agent_decision']['legacy_calls_without_reservation_ids'] == 656
    assert report.cost_scopes(roles)['subsequent_evaluations'] == {
        'calls': 69, 'settled_estimate_usd': .069, 'unsettled_reservations_usd': 0.0}


def test_task5_denominator_excludes_later_repeated_cases(tmp_path, monkeypatch):
    import json
    import evals.report as report
    snapshot = tmp_path / 'task5.json'
    snapshot.write_text(json.dumps({'recorded_batches': ['historical-dev', 'historical-regressions']}))
    monkeypatch.setattr(report, 'TASK5_SNAPSHOT', snapshot)
    rows = [
        {'batch': 'historical-dev', 'case_id': 'D01'},
        {'batch': 'historical-dev', 'case_id': 'D02'},
        {'batch': 'historical-regressions', 'case_id': 'S01'},
        {'batch': 'delivery-iteration-v7-paired', 'case_id': 'D01'},
        {'batch': 'later-fresh-batch', 'case_id': 'D03'},
    ]
    historical = report.task5_measurements(rows)
    assert [(r['batch'], r['case_id']) for r in historical] == [
        ('historical-dev', 'D01'), ('historical-dev', 'D02'), ('historical-regressions', 'S01')]
    assert len([r for r in historical if not r['case_id'].startswith('S')]) == 2
    assert len(rows) == 5
