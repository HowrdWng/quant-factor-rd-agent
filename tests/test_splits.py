import pytest
from quant_factor_agent.demo import synthetic_monthly_returns
from quant_factor_agent.domain.schemas import ApprovalDecision, ExperimentVerdict
from quant_factor_agent.evaluation import HoldoutAccessError, TemporalDataVault, TemporalSplit


def test_research_splits_do_not_overlap_holdout() -> None:
    vault = TemporalDataVault(synthetic_monthly_returns(periods=100))
    development = vault.frame(TemporalSplit.DEVELOPMENT)
    validation = vault.frame(TemporalSplit.VALIDATION)
    walk_forward = vault.frame(TemporalSplit.WALK_FORWARD)
    assert len(development) == 60
    assert len(validation) == 20
    assert len(walk_forward) == 10
    assert development.index.max() < validation.index.min() < walk_forward.index.min()
    with pytest.raises(HoldoutAccessError):
        vault.frame(TemporalSplit.HOLDOUT)


def test_holdout_token_is_approval_gated_and_one_shot() -> None:
    vault = TemporalDataVault(synthetic_monthly_returns(periods=100))
    with pytest.raises(HoldoutAccessError):
        vault.issue_holdout_token(
            "run-1",
            approval=ApprovalDecision.PENDING,
            verdict=ExperimentVerdict.ACCEPT,
        )
    token = vault.issue_holdout_token(
        "run-1",
        approval=ApprovalDecision.APPROVED,
        verdict=ExperimentVerdict.ACCEPT,
    )
    assert len(vault.consume_holdout("run-1", token)) == 10
    with pytest.raises(HoldoutAccessError):
        vault.consume_holdout("run-1", token)
