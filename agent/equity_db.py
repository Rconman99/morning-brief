"""SQLite state store for equity trading agent.

Tracks trades, positions, daily equity, and wash sales.
Replaces JSONL with proper database for performance queries and tax tracking.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)

DB_PATH = PROJECT_ROOT / "agent" / "equity_trades.db"


def get_conn() -> sqlite3.Connection:
    """Get a connection with row_factory for dict-like access."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            fill_price REAL,
            slippage REAL,
            commission REAL DEFAULT 0,
            cost_usd REAL,
            strategy TEXT,
            reason TEXT,
            conviction INTEGER,
            sector TEXT,
            mode TEXT DEFAULT 'paper',
            status TEXT DEFAULT 'pending',
            alpaca_order_id TEXT,
            run_id TEXT,
            filled_qty REAL DEFAULT 0,
            alpaca_status TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_equity (
            date TEXT PRIMARY KEY,
            portfolio_value REAL NOT NULL,
            cash REAL NOT NULL,
            invested REAL NOT NULL,
            daily_pnl REAL DEFAULT 0,
            daily_pnl_pct REAL DEFAULT 0,
            drawdown_pct REAL DEFAULT 0,
            peak_equity REAL,
            positions_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS wash_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            sell_date TEXT NOT NULL,
            sell_price REAL NOT NULL,
            loss_amount REAL NOT NULL,
            rebuy_window_end TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
        CREATE INDEX IF NOT EXISTS idx_wash_ticker ON wash_sales(ticker);
    """)

    # Idempotent migrations for columns added after the original schema
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for col, decl in [("filled_qty", "REAL DEFAULT 0"), ("alpaca_status", "TEXT")]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {decl}")

    conn.commit()
    conn.close()
    logger.info("Equity DB initialized at %s", DB_PATH)


def record_trade(trade: dict) -> int:
    """Insert a trade record. Returns the row ID."""
    conn = get_conn()
    cursor = conn.execute("""
        INSERT INTO trades (timestamp, ticker, side, quantity, price, fill_price,
                           slippage, commission, cost_usd, strategy, reason,
                           conviction, sector, mode, status, alpaca_order_id, run_id,
                           filled_qty, alpaca_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.get("timestamp", datetime.now().astimezone().isoformat()),
        trade["ticker"],
        trade["side"],
        trade["quantity"],
        trade["price"],
        trade.get("fill_price"),
        trade.get("slippage"),
        trade.get("commission", 0),
        trade.get("cost_usd", trade["quantity"] * trade["price"]),
        trade.get("strategy"),
        trade.get("reason"),
        trade.get("conviction"),
        trade.get("sector"),
        trade.get("mode", "paper"),
        trade.get("status", "pending"),
        trade.get("alpaca_order_id"),
        trade.get("run_id"),
        trade.get("filled_qty", 0),
        trade.get("alpaca_status"),
    ))
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_trades(days: int = 30) -> list[dict]:
    """Get trades from the last N days."""
    conn = get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC", (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_todays_trades() -> list[dict]:
    """Get all trades placed today."""
    conn = get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp", (today,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_positions_from_trades() -> dict:
    """Calculate net positions from trade history. Returns {ticker: {qty, avg_cost, sector}}."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT ticker, side, quantity, price, sector FROM trades "
        "WHERE status IN ('paper_filled', 'filled') ORDER BY timestamp"
    ).fetchall()
    conn.close()

    positions = {}
    for r in rows:
        ticker = r["ticker"]
        if ticker not in positions:
            positions[ticker] = {"qty": 0, "total_cost": 0, "sector": r["sector"]}

        if r["side"].upper() == "BUY":
            positions[ticker]["total_cost"] += r["quantity"] * r["price"]
            positions[ticker]["qty"] += r["quantity"]
        else:
            positions[ticker]["qty"] -= r["quantity"]

    # Clean up closed positions and calculate avg cost
    result = {}
    for ticker, pos in positions.items():
        if pos["qty"] > 0.01:  # Fractional share tolerance
            result[ticker] = {
                "qty": pos["qty"],
                "avg_cost": pos["total_cost"] / pos["qty"] if pos["qty"] > 0 else 0,
                "sector": pos["sector"],
            }
    return result


def get_daily_pnl() -> float:
    """Get today's realized P&L from trades."""
    trades = get_todays_trades()
    pnl = 0
    for t in trades:
        if t["side"].upper() == "SELL" and t.get("fill_price"):
            # Rough P&L: need to know cost basis, but for daily tracking
            # we record slippage costs
            pnl -= abs(t.get("slippage", 0) or 0) * t["quantity"]
    return pnl


def check_wash_sale(ticker: str) -> dict | None:
    """Check if buying this ticker would trigger a wash sale.

    Returns the wash sale record if within 30-day window, else None.
    """
    conn = get_conn()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT * FROM wash_sales WHERE ticker = ? AND rebuy_window_end >= ? "
        "ORDER BY sell_date DESC LIMIT 1",
        (ticker, today)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def record_wash_sale(ticker: str, sell_price: float, loss_amount: float) -> None:
    """Record a sale at a loss for wash sale tracking."""
    if loss_amount >= 0:
        return  # Not a loss, no wash sale concern

    sell_date = date.today()
    rebuy_end = sell_date + timedelta(days=30)
    conn = get_conn()
    conn.execute(
        "INSERT INTO wash_sales (ticker, sell_date, sell_price, loss_amount, rebuy_window_end) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticker, sell_date.isoformat(), sell_price, abs(loss_amount), rebuy_end.isoformat())
    )
    conn.commit()
    conn.close()
    logger.warning("WASH SALE WARNING: %s sold at loss $%.2f — rebuy blocked until %s",
                    ticker, abs(loss_amount), rebuy_end.isoformat())


def record_daily_equity(portfolio_value: float, cash: float, invested: float) -> None:
    """Record end-of-day portfolio snapshot."""
    conn = get_conn()
    today = date.today().isoformat()

    # Get previous day for P&L calc
    prev = conn.execute(
        "SELECT portfolio_value, peak_equity FROM daily_equity "
        "WHERE date < ? ORDER BY date DESC LIMIT 1", (today,)
    ).fetchone()

    prev_value = prev["portfolio_value"] if prev else portfolio_value
    peak = max(prev["peak_equity"] or prev_value, portfolio_value) if prev else portfolio_value
    daily_pnl = portfolio_value - prev_value
    daily_pnl_pct = daily_pnl / prev_value if prev_value > 0 else 0
    drawdown_pct = (peak - portfolio_value) / peak if peak > 0 else 0

    positions = get_open_positions_from_trades()

    conn.execute("""
        INSERT OR REPLACE INTO daily_equity
        (date, portfolio_value, cash, invested, daily_pnl, daily_pnl_pct,
         drawdown_pct, peak_equity, positions_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (today, portfolio_value, cash, invested, daily_pnl, daily_pnl_pct,
          drawdown_pct, peak, len(positions)))
    conn.commit()
    conn.close()


def get_peak_equity() -> float:
    """Get the all-time peak portfolio value for drawdown calculation."""
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(peak_equity) as peak FROM daily_equity"
    ).fetchone()
    conn.close()
    return row["peak"] if row and row["peak"] else 0


def get_current_drawdown() -> float:
    """Get current drawdown percentage from peak."""
    conn = get_conn()
    row = conn.execute(
        "SELECT drawdown_pct FROM daily_equity ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row["drawdown_pct"] if row else 0


def get_drawdown_pause_until() -> str | None:
    """Check if we're in a drawdown pause. Returns pause-until date or None."""
    from agent.equity_config import MAX_DRAWDOWN_PCT, MAX_DRAWDOWN_PAUSE_DAYS

    conn = get_conn()
    row = conn.execute(
        "SELECT date, drawdown_pct FROM daily_equity "
        "WHERE drawdown_pct >= ? ORDER BY date DESC LIMIT 1",
        (MAX_DRAWDOWN_PCT,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    breach_date = datetime.fromisoformat(row["date"])
    pause_until = breach_date + timedelta(days=MAX_DRAWDOWN_PAUSE_DAYS)
    if date.today() <= pause_until.date():
        return pause_until.date().isoformat()
    return None


def get_performance_metrics(days: int = 30) -> dict:
    """Calculate performance metrics over the last N days."""
    conn = get_conn()
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    trades = conn.execute(
        "SELECT * FROM trades WHERE timestamp >= ? AND status IN ('paper_filled', 'filled')",
        (cutoff,)
    ).fetchall()

    equity_rows = conn.execute(
        "SELECT daily_pnl_pct FROM daily_equity WHERE date >= ? ORDER BY date",
        (cutoff,)
    ).fetchall()
    conn.close()

    if not trades:
        return {"trades": 0, "win_rate": 0, "sharpe": 0, "total_pnl": 0}

    # Basic trade stats
    trade_count = len(trades)
    buys = [t for t in trades if t["side"].upper() == "BUY"]
    sells = [t for t in trades if t["side"].upper() == "SELL"]

    # Daily returns for Sharpe calculation
    daily_returns = [r["daily_pnl_pct"] for r in equity_rows if r["daily_pnl_pct"] is not None]

    sharpe = 0
    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        std_ret = (sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)) ** 0.5
        if std_ret > 0:
            sharpe = round((mean_ret / std_ret) * math.sqrt(252), 2)  # Annualized

    by_strategy = {}
    for t in trades:
        s = t["strategy"] or "unknown"
        if s not in by_strategy:
            by_strategy[s] = {"count": 0, "total_cost": 0}
        by_strategy[s]["count"] += 1
        by_strategy[s]["total_cost"] += t["cost_usd"] or 0

    return {
        "trades": trade_count,
        "buys": len(buys),
        "sells": len(sells),
        "sharpe": sharpe,
        "daily_returns": len(daily_returns),
        "by_strategy": by_strategy,
    }
