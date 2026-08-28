from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from quant_factor_agent.demo import synthetic_monthly_returns


class MarketDataRequest(BaseModel):
    market: str = "CN"
    universe: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    frequency: str = Field(default="daily", pattern="^(daily|weekly|monthly)$")
    fields: list[str] = Field(
        default_factory=lambda: ["open", "high", "low", "close", "volume", "returns"]
    )


@dataclass(frozen=True)
class MarketDataBundle:
    frames: dict[str, pd.DataFrame]
    provider: str
    metadata: dict[str, object]

    @property
    def returns(self) -> pd.DataFrame:
        if "returns" not in self.frames:
            raise KeyError("market data bundle does not contain returns")
        return self.frames["returns"]

    def validate(self) -> None:
        if not self.frames:
            raise ValueError("market data bundle is empty")
        reference = next(iter(self.frames.values()))
        if reference.empty:
            raise ValueError("market data contains no rows")
        for name, frame in self.frames.items():
            if not frame.index.equals(reference.index) or list(frame.columns) != list(reference.columns):
                raise ValueError(f"field {name} does not align with the shared date/asset panel")


class MarketDataProvider(Protocol):
    name: str

    def describe(self) -> dict[str, object]: ...

    def load(self, request: MarketDataRequest) -> MarketDataBundle: ...


class SyntheticMarketDataProvider:
    name = "synthetic"

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ready": True,
            "real_data": False,
            "fields": ["close", "volume", "returns"],
        }

    def load(self, request: MarketDataRequest) -> MarketDataBundle:
        monthly = synthetic_monthly_returns()
        close = 100.0 * (1.0 + monthly).cumprod()
        volume = pd.DataFrame(
            np.linspace(800_000, 1_200_000, len(monthly))[:, None]
            * np.ones((len(monthly), len(monthly.columns))),
            index=monthly.index,
            columns=monthly.columns,
        )
        frames = {"close": close, "volume": volume, "returns": monthly}
        bundle = MarketDataBundle(frames=frames, provider=self.name, metadata={"synthetic": True})
        bundle.validate()
        return bundle


class LocalCsmarCsvProvider:
    """Read exported CSMAR CSVs through one provider boundary.

    The provider supports both the normalized quotation schema and the classic
    TRD_Dalyr schema. Quant Engine code therefore does not depend on either.
    """

    name = "local_csmar_csv"
    CANDIDATES = ("trd_fwardquotation.csv", "trd_bwardquotation.csv", "trd_dalyr.csv")

    NORMALIZED_COLUMNS = {
        "TradingDate": "date", "Symbol": "asset", "OpenPrice": "open",
        "HighPrice": "high", "LowPrice": "low", "ClosePrice": "close",
        "Volume": "volume", "Amount": "amount", "ChangeRatio": "returns",
        "Stkcd": "asset", "Trddt": "date", "Opnprc": "open", "Hiprc": "high",
        "Loprc": "low", "Clsprc": "close", "Dnshrtrd": "volume",
        "Dnvaltrd": "amount", "Dretwd": "returns",
    }

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _source(self) -> Path | None:
        for name in self.CANDIDATES:
            path = self.root / "csmar_exports" / name
            if path.exists() and path.stat().st_size > 0:
                return path
        return None

    def describe(self) -> dict[str, object]:
        source = self._source()
        return {"name": self.name, "ready": source is not None, "real_data": True,
                "source": str(source) if source else None,
                "fields": ["open", "high", "low", "close", "volume", "amount", "returns"]}

    @staticmethod
    def _encoding(path: Path) -> str:
        prefix = path.read_bytes()[:4]
        return "utf-8-sig" if prefix.startswith(b"\xef\xbb\xbf") else "utf-8"

    def load(self, request: MarketDataRequest) -> MarketDataBundle:
        source = self._source()
        if source is None:
            raise FileNotFoundError("no exported CSMAR OHLCV CSV is ready")
        if request.market.upper() not in {"CN", "A", "ASHARE"}:
            raise ValueError("local CSMAR provider contains China A-share data, not the requested market")
        if source.stat().st_size > 250 * 1024 * 1024 and not (
            request.start_date or request.end_date or request.universe
        ):
            raise ValueError(
                "large local CSV requests require a date range or explicit universe to bound memory"
            )
        selected: list[pd.DataFrame] = []
        encoding = self._encoding(source)
        for chunk in pd.read_csv(source, chunksize=250_000, encoding=encoding, low_memory=False):
            chunk = chunk.rename(columns=self.NORMALIZED_COLUMNS)
            if not {"date", "asset", "close"}.issubset(chunk.columns):
                raise ValueError(f"unsupported CSMAR columns in {source.name}")
            chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
            chunk["asset"] = chunk["asset"].astype(str).str.zfill(6)
            if request.start_date:
                chunk = chunk[chunk["date"] >= pd.Timestamp(request.start_date)]
            if request.end_date:
                chunk = chunk[chunk["date"] <= pd.Timestamp(request.end_date)]
            if request.universe:
                universe = {str(item).zfill(6) for item in request.universe}
                chunk = chunk[chunk["asset"].isin(universe)]
            if not chunk.empty:
                selected.append(chunk)
        if not selected:
            raise ValueError("no local CSMAR rows matched the market data request")
        tidy = pd.concat(selected, ignore_index=True).dropna(subset=["date", "asset"])
        tidy = tidy.sort_values(["date", "asset"]).drop_duplicates(["date", "asset"], keep="last")
        available = [field for field in request.fields if field in tidy.columns]
        if "returns" not in tidy.columns:
            tidy["returns"] = tidy.groupby("asset")["close"].pct_change(fill_method=None)
            available.append("returns")
        frames = {
            field: tidy.pivot(index="date", columns="asset", values=field).sort_index()
            for field in dict.fromkeys(available)
        }
        if request.frequency != "daily":
            rule = "W-FRI" if request.frequency == "weekly" else "ME"
            resampled: dict[str, pd.DataFrame] = {}
            for field, frame in frames.items():
                if field == "volume" or field == "amount":
                    resampled[field] = frame.resample(rule).sum(min_count=1)
                elif field == "returns":
                    resampled[field] = (1.0 + frame).resample(rule).prod(min_count=1) - 1.0
                else:
                    resampled[field] = frame.resample(rule).last()
            frames = resampled
        common_index = next(iter(frames.values())).index
        common_columns = next(iter(frames.values())).columns
        frames = {
            name: frame.reindex(index=common_index, columns=common_columns)
            for name, frame in frames.items()
        }
        bundle = MarketDataBundle(
            frames=frames, provider=self.name,
            metadata={"source": str(source.resolve()), "rows": len(tidy), "assets": tidy["asset"].nunique()},
        )
        bundle.validate()
        return bundle
