from quant_factor_agent.domain.schemas import RunRecord, RunStatus
from quant_factor_agent.storage import ExperimentStore


def test_store_persists_ordered_events_and_metrics(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "experiments.db")
    store.create_run(RunRecord(run_id="run-1", objective="test factor", status=RunStatus.CREATED))
    store.log_event("run-1", "created", {"value": 1})
    store.log_event("run-1", "evaluated", {"value": 2})
    store.log_metrics("run-1", {"mean_ic": 0.05}, step=0)
    store.update_run("run-1", status=RunStatus.ACCEPTED)

    assert store.get_run("run-1")["status"] == "accepted"
    events = store.list_events("run-1")
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[1]["payload"] == {"value": 2}
    assert store.list_runs(status=RunStatus.ACCEPTED)[0]["run_id"] == "run-1"
    assert store.get_metrics("run-1")[0]["value"] == 0.05
