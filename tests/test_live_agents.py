from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from quant_factor_agent.agents.live import LLMDevelopmentAgent, LLMResearchAgent
from quant_factor_agent.cli import build_workflow
from quant_factor_agent.demo import demo_evidence
from quant_factor_agent.domain.schemas import FactorHypothesis
from quant_factor_agent.llm import LLMHealthCheck, ModelProvider, OpenAICompatibleProvider

TModel = TypeVar("TModel", bound=BaseModel)


class FakeProvider(ModelProvider):
    provider_name = "fake"
    model_name = "fake-model"

    def generate_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        temperature: float = 0.2,
    ) -> TModel:
        if schema is FactorHypothesis:
            evidence_id = demo_evidence()[0].evidence_id
            return schema.model_validate(
                {
                    "title": "Lagged quality momentum",
                    "thesis": "Assets with stronger lagged medium-term returns may outperform next month.",
                    "economic_rationale": (
                        "Underreaction can make price trends persist after public information arrives."
                    ),
                    "formula_description": "Rank six-month compounded returns lagged by one month.",
                    "required_fields": ["monthly_returns"],
                    "expected_direction": "positive",
                    "lookback_periods": 6,
                    "lag_periods": 1,
                    "evidence_ids": [evidence_id],
                    "leakage_risks": ["Using formation-period returns"],
                    "falsification_criteria": ["Validation Rank IC is not positive"],
                }
            )
        return schema.model_validate(
            {
                "name": "momentum_6m_lag1",
                "description": "Lagged six-month compounded return factor.",
                "lookback_periods": 6,
                "lag_periods": 1,
                "parameters": {"window": 6},
                "code": (
                    "def compute_factor(monthly_returns):\n"
                    "    shifted = monthly_returns.shift(1)\n"
                    "    return (1.0 + shifted).rolling(6, min_periods=6).apply(np.prod, raw=True) - 1.0\n"
                ),
            }
        )


def test_live_agents_build_valid_domain_objects() -> None:
    evidence = demo_evidence()
    provider = FakeProvider()
    hypothesis = LLMResearchAgent(provider).propose("Research momentum", evidence)
    implementation = LLMDevelopmentAgent(provider).implement(hypothesis)

    assert hypothesis.lookback_periods == 6
    assert implementation.source == "llm_agent"
    assert implementation.spec.name == "momentum_6m_lag1"
    assert implementation.code_sha256


def test_live_provider_runs_the_complete_research_pipeline(tmp_path) -> None:
    with build_workflow(
        tmp_path,
        mode="live",
        model_provider=FakeProvider(),
        use_local_reports=False,
    ) as workflow:
        result = workflow.run(
            "Research a robust lagged cross-sectional momentum factor",
            auto_approve=True,
            max_iterations=0,
        )

    assert result["status"] in {"accepted", "rejected"}
    assert result["implementation"]["source"] == "llm_agent"
    assert result["evaluation"]["n_periods"] > 0


def test_official_openai_payload_uses_json_schema() -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model_name="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        provider_name="openai",
    )

    responses = provider._responses_payload(
        system="system",
        user="user",
        schema=LLMHealthCheck,
        temperature=0.2,
    )
    chat = provider._chat_payload(
        system="system",
        user="user",
        schema=LLMHealthCheck,
        temperature=0.2,
    )

    assert responses["text"]["format"]["type"] == "json_schema"
    assert chat["response_format"]["type"] == "json_schema"


def test_compatible_payload_keeps_json_object_fallback() -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model_name="vendor-model",
        base_url="https://vendor.example/v1",
    )

    payload = provider._chat_payload(
        system="system",
        user="user",
        schema=LLMHealthCheck,
        temperature=0.2,
    )

    assert payload["response_format"] == {"type": "json_object"}


def test_responses_success_with_null_error_is_not_rejected(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        model_name="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        provider_name="openai",
        api_style="responses",
    )
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "_post",
        lambda self, endpoint, payload: {
            "error": None,
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"status":"ok","message":"model reachable"}',
                        }
                    ]
                }
            ],
        },
    )

    health = provider.health_check()

    assert health.status == "ok"
