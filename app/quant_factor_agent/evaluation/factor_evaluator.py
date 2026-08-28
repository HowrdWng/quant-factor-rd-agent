from __future__ import annotations

import numpy as np
import pandas as pd

from quant_factor_agent.domain.schemas import FactorEvaluation


class FactorEvaluator:
    """Cross-sectional factor diagnostics with an explicit next-period target."""

    def __init__(
        self,
        quantile_fraction: float = 0.25,
        periods_per_year: int = 12,
        forward_periods: int = 1,
    ) -> None:
        if not 0 < quantile_fraction <= 0.5:
            raise ValueError("quantile_fraction must be in (0, 0.5]")
        self.quantile_fraction = quantile_fraction
        if periods_per_year < 1:
            raise ValueError("periods_per_year must be positive")
        self.periods_per_year = periods_per_year
        if forward_periods < 1:
            raise ValueError("forward_periods must be positive")
        self.forward_periods = forward_periods

    def evaluate(
        self,
        factor: pd.DataFrame,
        monthly_returns: pd.DataFrame,
        *,
        split: str = "development",
        passed_leakage_checks: bool = True,
    ) -> FactorEvaluation:
        common_dates = factor.index.intersection(monthly_returns.index)
        common_assets = factor.columns.intersection(monthly_returns.columns)
        scores = factor.loc[common_dates, common_assets]
        # Factor row t is formed before period t and is required by the runtime
        # policy to use data no later than t-1. It therefore predicts the return
        # realized in the same indexed period t. This convention matches the
        # existing portfolio engine's weight_t * return_t accounting.
        single_period_returns = monthly_returns.loc[common_dates, common_assets]
        if self.forward_periods == 1:
            realized_returns = single_period_returns
        else:
            realized_returns = (
                (1.0 + single_period_returns)
                .rolling(self.forward_periods, min_periods=self.forward_periods)
                .apply(np.prod, raw=True)
                .shift(-(self.forward_periods - 1))
                - 1.0
            )

        ics: list[float] = []
        spreads: list[float] = []
        holdings: list[pd.Series] = []
        quantile_returns: list[list[float]] = [[] for _ in range(5)]
        valid_cells = 0
        total_cells = int(scores.shape[0] * scores.shape[1])

        for date in common_dates:
            score_row, return_row = scores.loc[date].align(realized_returns.loc[date], join="inner")
            valid = score_row.notna() & return_row.notna()
            score_row = score_row[valid]
            return_row = return_row[valid]
            valid_cells += int(valid.sum())
            if len(score_row) < 4 or score_row.nunique() < 2:
                continue

            ic = score_row.corr(return_row, method="spearman")
            if pd.notna(ic):
                ics.append(float(ic))

            bucket_size = max(1, int(len(score_row) * self.quantile_fraction))
            ranked = score_row.rank(method="first", ascending=False)
            long_assets = ranked[ranked <= bucket_size].index
            short_assets = ranked[ranked > len(score_row) - bucket_size].index
            spreads.append(float(return_row[long_assets].mean() - return_row[short_assets].mean()))
            if len(score_row) >= 5:
                buckets = pd.qcut(score_row.rank(method="first"), 5, labels=False)
                for bucket in range(5):
                    selected = return_row[buckets == bucket]
                    if not selected.empty:
                        quantile_returns[bucket].append(float(selected.mean()))
            holding = pd.Series(0.0, index=common_assets)
            holding.loc[long_assets] = 1.0 / len(long_assets)
            holdings.append(holding)

        if not ics:
            raise ValueError("evaluation produced no valid cross-sectional periods")

        ic_array = np.asarray(ics)
        ic_std = float(ic_array.std(ddof=1)) if len(ic_array) > 1 else 0.0
        mean_ic = float(ic_array.mean())
        weight_frame = pd.DataFrame(holdings)
        turnover = (
            float(weight_frame.diff().abs().sum(axis=1).iloc[1:].mean() / 2) if len(weight_frame) > 1 else 0.0
        )
        spread_series = pd.Series(spreads, dtype=float)
        equity = (1.0 + spread_series).cumprod()
        drawdown = equity / equity.cummax() - 1.0
        volatility = float(spread_series.std(ddof=1)) if len(spread_series) > 1 else 0.0
        sharpe = (
            float(spread_series.mean() / volatility * np.sqrt(self.periods_per_year))
            if volatility > 0
            else 0.0
        )
        annualized_return = (
            float(equity.iloc[-1] ** (self.periods_per_year / len(equity)) - 1.0)
            if len(equity) and equity.iloc[-1] > 0
            else -1.0
        )
        annualized_quantiles = [
            float(np.mean(values) * self.periods_per_year) if values else 0.0
            for values in quantile_returns
        ]
        quantile_correlation = pd.Series(range(5), dtype=float).corr(
            pd.Series(annualized_quantiles, dtype=float), method="spearman"
        )
        quantile_monotonicity = float(quantile_correlation) if pd.notna(quantile_correlation) else 0.0

        return FactorEvaluation(
            mean_rank_ic=mean_ic,
            icir=mean_ic / ic_std if ic_std > 0 else 0.0,
            positive_ic_rate=float((ic_array > 0).mean()),
            top_bottom_spread_annualized=float(np.mean(spreads) * self.periods_per_year),
            annualized_return=annualized_return,
            annualized_volatility=volatility * float(np.sqrt(self.periods_per_year)),
            sharpe=sharpe,
            max_drawdown=float(drawdown.min()) if not drawdown.empty else 0.0,
            quantile_returns_annualized=annualized_quantiles,
            quantile_monotonicity=quantile_monotonicity,
            one_way_turnover=turnover,
            coverage=valid_cells / max(1, total_cells),
            n_periods=len(ics),
            passed_leakage_checks=passed_leakage_checks,
            forward_periods=self.forward_periods,
            evaluation_split=split,
        )
