from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from quant_factor_agent.domain.schemas import ApprovalDecision, ExperimentVerdict


class TemporalSplit(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    WALK_FORWARD = "walk_forward"
    HOLDOUT = "holdout"


class HoldoutAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class SplitBoundaries:
    development_end: int
    validation_end: int
    walk_forward_end: int
    total: int


class TemporalDataVault:
    """Owns chronological splits and enforces one-shot holdout access.

    The R&D graph can request development, validation, and walk-forward data.
    Holdout data requires a short-lived token issued only after an approved,
    accepted experiment and is consumed on first use.
    """

    def __init__(
        self,
        monthly_returns: pd.DataFrame,
        *,
        development_fraction: float = 0.60,
        validation_fraction: float = 0.20,
        walk_forward_fraction: float = 0.10,
    ) -> None:
        if monthly_returns.empty or len(monthly_returns) < 40:
            raise ValueError("at least 40 ordered periods are required")
        if not monthly_returns.index.is_monotonic_increasing or not monthly_returns.index.is_unique:
            raise ValueError("return index must be unique and chronologically sorted")
        research_fraction = development_fraction + validation_fraction + walk_forward_fraction
        if not 0 < development_fraction < research_fraction < 1:
            raise ValueError("split fractions must leave a positive locked holdout")
        if min(development_fraction, validation_fraction, walk_forward_fraction) <= 0:
            raise ValueError("every research split must be positive")

        total = len(monthly_returns)
        development_end = int(total * development_fraction)
        validation_end = development_end + int(total * validation_fraction)
        walk_forward_end = validation_end + int(total * walk_forward_fraction)
        self.boundaries = SplitBoundaries(
            development_end=development_end,
            validation_end=validation_end,
            walk_forward_end=walk_forward_end,
            total=total,
        )
        self._returns = monthly_returns.copy()
        self._holdout_tokens: dict[str, str] = {}

    def frame(self, split: TemporalSplit) -> pd.DataFrame:
        bounds = self.boundaries
        slices = {
            TemporalSplit.DEVELOPMENT: slice(0, bounds.development_end),
            TemporalSplit.VALIDATION: slice(bounds.development_end, bounds.validation_end),
            TemporalSplit.WALK_FORWARD: slice(bounds.validation_end, bounds.walk_forward_end),
        }
        if split == TemporalSplit.HOLDOUT:
            raise HoldoutAccessError("holdout requires a one-shot access token")
        return self._returns.iloc[slices[split]].copy()

    def research_frame(self) -> pd.DataFrame:
        """All non-holdout history, used to warm rolling factor calculations."""
        return self._returns.iloc[: self.boundaries.walk_forward_end].copy()

    def issue_holdout_token(
        self,
        run_id: str,
        *,
        approval: ApprovalDecision,
        verdict: ExperimentVerdict,
    ) -> str:
        if approval != ApprovalDecision.APPROVED or verdict != ExperimentVerdict.ACCEPT:
            raise HoldoutAccessError("holdout unlock requires approved and accepted research")
        token = secrets.token_urlsafe(32)
        self._holdout_tokens[token] = run_id
        return token

    def consume_holdout(self, run_id: str, token: str) -> pd.DataFrame:
        owner = self._holdout_tokens.pop(token, None)
        if owner != run_id:
            raise HoldoutAccessError("invalid, mismatched, or already consumed holdout token")
        return self._returns.iloc[self.boundaries.walk_forward_end :].copy()
