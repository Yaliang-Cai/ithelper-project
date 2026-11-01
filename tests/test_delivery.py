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
