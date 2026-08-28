from fastapi.testclient import TestClient
from quant_factor_agent import __version__
from quant_factor_agent.web import app


def test_web_health_and_index() -> None:
    client = TestClient(app)
    assert client.get("/").status_code == 200
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["service"] == "quant-factor-rd-agent"
    assert health.json()["version"] == __version__ == "0.3.0"


def test_learning_experiment_uses_quant_engine() -> None:
    response = TestClient(app).post("/api/learn/experiment", json={})
    assert response.status_code == 200
    assert response.json()["evaluation"]["n_periods"] > 0
