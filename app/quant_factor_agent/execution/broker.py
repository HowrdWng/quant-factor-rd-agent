from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0)


class RiskRules(BaseModel):
    max_order_notional: float = Field(default=100_000.0, gt=0)
    max_position_notional: float = Field(default=250_000.0, gt=0)
    allow_short: bool = False


class BrokerAdapter(Protocol):
    name: str

    def get_account(self) -> dict[str, object]: ...

    def get_positions(self) -> list[dict[str, object]]: ...

    def get_market_data(self, symbols: list[str]) -> dict[str, float]: ...

    def get_orders(self) -> list[dict[str, object]]: ...

    def place_order(self, order: OrderRequest) -> dict[str, object]: ...

    def cancel_order(self, order_id: str) -> dict[str, object]: ...


class PaperBrokerAdapter:
    """Local paper broker. LLM code never receives this object."""

    name = "paper"

    def __init__(
        self,
        path: str | Path,
        *,
        initial_cash: float = 1_000_000.0,
        risk_rules: RiskRules | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_cash = initial_cash
        self.risk_rules = risk_rules or RiskRules()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_account (
                    account_id INTEGER PRIMARY KEY CHECK(account_id=1),
                    cash REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_prices (
                    symbol TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL,
                    average_price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    order_type TEXT NOT NULL,
                    limit_price REAL,
                    fill_price REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO paper_account(account_id, cash) VALUES (1, ?)",
                (self.initial_cash,),
            )

    def update_market_prices(self, prices: dict[str, float]) -> None:
        now = datetime.now(UTC).isoformat()
        rows = [(symbol.upper(), float(price), now) for symbol, price in prices.items() if price > 0]
        with self.connect() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO paper_prices(symbol, price, updated_at)
                VALUES (?, ?, ?)""",
                rows,
            )

    def get_market_data(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT symbol, price FROM paper_prices WHERE symbol IN ({placeholders})",  # noqa: S608
                [symbol.upper() for symbol in symbols],
            ).fetchall()
        return {row["symbol"]: float(row["price"]) for row in rows}

    def get_positions(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT p.symbol, p.quantity, p.average_price,
                COALESCE(m.price, p.average_price) AS market_price
                FROM paper_positions p LEFT JOIN paper_prices m ON m.symbol=p.symbol
                WHERE ABS(p.quantity) > 0.0000001 ORDER BY p.symbol"""
            ).fetchall()
        return [
            {
                **dict(row),
                "market_value": float(row["quantity"] * row["market_price"]),
                "unrealized_pnl": float(row["quantity"] * (row["market_price"] - row["average_price"])),
            }
            for row in rows
        ]

    def get_account(self) -> dict[str, object]:
        with self.connect() as connection:
            cash = float(
                connection.execute("SELECT cash FROM paper_account WHERE account_id=1").fetchone()[0]
            )
        positions = self.get_positions()
        market_value = sum(float(item["market_value"]) for item in positions)
        return {"broker": self.name, "cash": cash, "market_value": market_value,
                "equity": cash + market_value, "currency": "USD", "live_trading": False}

    def get_orders(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM paper_orders ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def place_order(self, order: OrderRequest) -> dict[str, object]:
        symbol = order.symbol.upper()
        prices = self.get_market_data([symbol])
        price = prices.get(symbol)
        if price is None:
            raise ValueError(f"no paper market price for {symbol}")
        if order.order_type == "limit":
            crosses = (order.side == "buy" and price <= float(order.limit_price)) or (
                order.side == "sell" and price >= float(order.limit_price)
            )
            if not crosses:
                return self._record_pending(order, symbol)
        notional = price * order.quantity
        if notional > self.risk_rules.max_order_notional:
            raise ValueError("order exceeds max_order_notional risk rule")
        now = datetime.now(UTC).isoformat()
        order_id = uuid4().hex
        with self.connect() as connection:
            account = connection.execute("SELECT cash FROM paper_account WHERE account_id=1").fetchone()
            position = connection.execute(
                "SELECT quantity, average_price FROM paper_positions WHERE symbol=?", (symbol,)
            ).fetchone()
            old_quantity = float(position["quantity"]) if position else 0.0
            old_average = float(position["average_price"]) if position else 0.0
            signed = order.quantity if order.side == "buy" else -order.quantity
            new_quantity = old_quantity + signed
            if not self.risk_rules.allow_short and new_quantity < -0.0000001:
                raise ValueError("paper risk rules do not allow short positions")
            if abs(new_quantity * price) > self.risk_rules.max_position_notional:
                raise ValueError("resulting position exceeds max_position_notional")
            cash_change = -signed * price
            new_cash = float(account["cash"]) + cash_change
            if new_cash < 0:
                raise ValueError("insufficient paper cash")
            if new_quantity > old_quantity and new_quantity > 0:
                new_average = (old_quantity * old_average + signed * price) / new_quantity
            else:
                new_average = price if abs(new_quantity) < 0.0000001 else old_average
            connection.execute("UPDATE paper_account SET cash=? WHERE account_id=1", (new_cash,))
            connection.execute(
                """INSERT OR REPLACE INTO paper_positions(symbol, quantity, average_price, updated_at)
                VALUES (?, ?, ?, ?)""", (symbol, new_quantity, new_average, now)
            )
            connection.execute(
                """INSERT INTO paper_orders(order_id, symbol, side, quantity, order_type,
                limit_price, fill_price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'filled', ?, ?)""",
                (order_id, symbol, order.side, order.quantity, order.order_type,
                 order.limit_price, price, now, now),
            )
        return next(item for item in self.get_orders() if item["order_id"] == order_id)

    def _record_pending(self, order: OrderRequest, symbol: str) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        order_id = uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO paper_orders(order_id, symbol, side, quantity, order_type,
                limit_price, fill_price, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', ?, ?)""",
                (order_id, symbol, order.side, order.quantity, order.order_type,
                 order.limit_price, now, now),
            )
        return next(item for item in self.get_orders() if item["order_id"] == order_id)

    def cancel_order(self, order_id: str) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            result = connection.execute(
                """UPDATE paper_orders SET status='cancelled', updated_at=?
                WHERE order_id=? AND status='pending'""",
                (now, order_id),
            )
            if result.rowcount != 1:
                raise ValueError("only a pending paper order can be cancelled")
        return next(item for item in self.get_orders() if item["order_id"] == order_id)
