from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from quant_factor_agent.evaluation.factor_evaluator import FactorEvaluator


def _rank_zscore(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = frame.rank(axis=1, pct=True, method="average")
    return (ranked - 0.5) / 0.28867513459481287


class MultiFactorEngine:
    """Explainable equal/IC-weighted combination without parameter search."""

    def __init__(self, *, redundancy_threshold: float = 0.80, quantile_fraction: float = 0.20) -> None:
        self.redundancy_threshold = redundancy_threshold
        self.evaluator = FactorEvaluator(quantile_fraction=quantile_fraction)

    def analyze(
        self,
        factors: dict[str, pd.DataFrame],
        returns: pd.DataFrame,
        *,
        method: str = "equal_weight",
        periods_per_year: int = 12,
    ) -> dict[str, object]:
        if len(factors) < 2:
            raise ValueError("multi-factor analysis requires at least two factors")
        aligned = {
            name: frame.reindex(index=returns.index, columns=returns.columns)
            for name, frame in factors.items()
        }
        correlations: dict[str, float] = {}
        redundant: list[dict[str, object]] = []
        for left, right in combinations(aligned, 2):
            values: list[float] = []
            for date in returns.index:
                pair = pd.concat([aligned[left].loc[date], aligned[right].loc[date]], axis=1).dropna()
                if len(pair) >= 4:
                    value = pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman")
                    if pd.notna(value):
                        values.append(float(value))
            mean_correlation = float(np.mean(values)) if values else 0.0
            correlations[f"{left}__{right}"] = mean_correlation
            if abs(mean_correlation) >= self.redundancy_threshold:
                redundant.append({"left": left, "right": right, "correlation": mean_correlation})

        evaluations = {
            name: self.evaluator.evaluate(frame, returns, split="validation").model_dump(mode="json")
            for name, frame in aligned.items()
        }
        if method == "ic_weighted":
            raw = {name: max(0.0, float(evaluations[name]["mean_rank_ic"])) for name in aligned}
            total = sum(raw.values())
            weights = (
                {name: value / total for name, value in raw.items()}
                if total > 0
                else {name: 1.0 / len(aligned) for name in aligned}
            )
        elif method == "equal_weight":
            weights = {name: 1.0 / len(aligned) for name in aligned}
        else:
            raise ValueError("method must be equal_weight or ic_weighted")

        combined = sum(_rank_zscore(frame) * weights[name] for name, frame in aligned.items())
        combined_evaluation = self.evaluator.evaluate(
            combined, returns, split="validation"
        ).model_dump(mode="json")
        portfolio_returns, turnover = self._long_only_backtest(combined, returns)
        backtest = self._performance(portfolio_returns, periods_per_year=periods_per_year)
        backtest["one_way_turnover"] = turnover
        return {
            "method": method,
            "weights": weights,
            "factor_correlation": correlations,
            "redundancy": redundant,
            "ic_comparison": evaluations,
            "combined_evaluation": combined_evaluation,
            "backtest": backtest,
            "combined_factor": combined,
        }

    def _long_only_backtest(
        self, scores: pd.DataFrame, returns: pd.DataFrame
    ) -> tuple[pd.Series, float]:
        weights: list[pd.Series] = []
        realized: list[float] = []
        dates: list[object] = []
        for date in scores.index:
            row = scores.loc[date].dropna()
            return_row = returns.loc[date].reindex(row.index).dropna()
            row = row.reindex(return_row.index)
            if len(row) < 4:
                continue
            count = max(1, int(len(row) * self.evaluator.quantile_fraction))
            assets = row.nlargest(count).index
            weight = pd.Series(0.0, index=scores.columns)
            weight.loc[assets] = 1.0 / len(assets)
            weights.append(weight)
            realized.append(float(return_row.loc[assets].mean()))
            dates.append(date)
        frame = pd.DataFrame(weights, index=dates)
        turnover = float(frame.diff().abs().sum(axis=1).iloc[1:].mean() / 2) if len(frame) > 1 else 0.0
        return pd.Series(realized, index=dates, dtype=float), turnover

    @staticmethod
    def _performance(returns: pd.Series, *, periods_per_year: int) -> dict[str, float]:
        if returns.empty:
            raise ValueError("backtest produced no portfolio periods")
        equity = (1.0 + returns.fillna(0.0)).cumprod()
        drawdown = equity / equity.cummax() - 1.0
        volatility = float(returns.std(ddof=1))
        sharpe = float(returns.mean() / volatility * np.sqrt(periods_per_year)) if volatility > 0 else 0.0
        return {
            "annualized_return": float(equity.iloc[-1] ** (periods_per_year / len(equity)) - 1.0),
            "annualized_volatility": volatility * float(np.sqrt(periods_per_year)),
            "sharpe": sharpe,
            "max_drawdown": float(drawdown.min()),
            "periods": float(len(returns)),
        }
