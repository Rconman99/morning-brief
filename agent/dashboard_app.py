"""Trading Agent Dashboard — Streamlit app.

Shows equity curve, open positions, trade history, strategy P&L attribution,
and risk gate status. Reads from equity_trades.db (SQLite).

Run: .venv/bin/python -m streamlit run agent/dashboard_app.py
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3
import json
from datetime import datetime, date, timedelta

try:
    import streamlit as st
    import pandas as pd
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False
    print("Install streamlit: .venv/bin/pip install streamlit")
    sys.exit(1)

from agent.equity_db import DB_PATH, get_trades, get_performance_metrics, get_open_positions_from_trades

# Page config
st.set_page_config(
    page_title="Trading Agent",
    page_icon="📈",
    layout="wide",
)

st.title("📈 Equity Trading Agent")
st.caption(f"Data from: {DB_PATH}")


def get_conn():
    return sqlite3.connect(str(DB_PATH))


# --- ACCOUNT OVERVIEW ---
st.header("Account Overview")

try:
    import os
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    from agent.equity_executor import get_account, get_positions

    acct = get_account()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Equity", f"${acct.get('equity', 0):,.2f}")
    col2.metric("Buying Power", f"${acct.get('buying_power', 0):,.2f}")
    col3.metric("Cash", f"${acct.get('cash', 0):,.2f}")
    col4.metric("Status", str(acct.get('status', 'offline')).split('.')[-1])
except Exception as e:
    st.warning(f"Alpaca not connected: {e}")
    acct = {}


# --- OPEN POSITIONS ---
st.header("Open Positions")

try:
    positions = get_positions()
    if positions:
        pos_df = pd.DataFrame(positions)
        display_cols = [c for c in ["ticker", "qty", "avg_cost", "current_price", "market_value",
                                     "unrealized_pnl", "unrealized_pnl_pct"] if c in pos_df.columns]
        st.dataframe(pos_df[display_cols], use_container_width=True)
    else:
        st.info("No open positions")
except Exception:
    # Fallback to DB-derived positions
    db_positions = get_open_positions_from_trades()
    if db_positions:
        pos_data = [{"ticker": t, "qty": p["qty"], "avg_cost": f"${p['avg_cost']:.2f}"}
                    for t, p in db_positions.items()]
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True)
    else:
        st.info("No open positions")


# --- EQUITY CURVE ---
st.header("Equity Curve")

conn = get_conn()
equity_df = pd.read_sql_query(
    "SELECT date, portfolio_value, daily_pnl, drawdown_pct FROM daily_equity ORDER BY date",
    conn
)
conn.close()

if not equity_df.empty:
    equity_df["date"] = pd.to_datetime(equity_df["date"])

    col1, col2 = st.columns(2)
    with col1:
        st.line_chart(equity_df.set_index("date")["portfolio_value"])
    with col2:
        st.bar_chart(equity_df.set_index("date")["daily_pnl"])

    # Drawdown chart
    st.subheader("Drawdown")
    st.area_chart(equity_df.set_index("date")["drawdown_pct"])
else:
    st.info("No equity history yet — will populate after the first trading day")


# --- TRADE HISTORY ---
st.header("Trade History")

days = st.selectbox("Show trades from last:", [7, 14, 30, 90], index=2)
trades = get_trades(days=days)

if trades:
    trades_df = pd.DataFrame(trades)
    display_cols = [c for c in ["timestamp", "ticker", "side", "quantity", "price",
                                 "cost_usd", "strategy", "status", "conviction"]
                    if c in trades_df.columns]
    trades_df["timestamp"] = pd.to_datetime(trades_df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")

    # Color-code by side
    st.dataframe(trades_df[display_cols], use_container_width=True)

    # Strategy breakdown
    st.subheader("By Strategy")
    strategy_counts = trades_df.groupby("strategy").agg(
        trades=("ticker", "count"),
        total_cost=("cost_usd", "sum"),
    ).reset_index()
    st.dataframe(strategy_counts, use_container_width=True)
else:
    st.info(f"No trades in the last {days} days")


# --- PERFORMANCE METRICS ---
st.header("Performance Metrics")

metrics = get_performance_metrics(days=30)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Trades", metrics.get("trades", 0))
col2.metric("Buys", metrics.get("buys", 0))
col3.metric("Sells", metrics.get("sells", 0))
col4.metric("Sharpe Ratio", f"{metrics.get('sharpe', 0):.2f}")


# --- RISK STATUS ---
st.header("Risk Gate Status")

from agent.equity_config import (
    MAX_SINGLE_POSITION_PCT, MAX_TOTAL_EXPOSURE_PCT,
    MAX_CONCURRENT_POSITIONS, MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT,
)
from agent.equity_db import get_current_drawdown, get_drawdown_pause_until

drawdown = get_current_drawdown()
pause = get_drawdown_pause_until()

risk_data = {
    "Limit": ["Single Position", "Total Exposure", "Concurrent Positions",
              "Daily Loss", "Drawdown", "Drawdown Pause"],
    "Threshold": [f"{MAX_SINGLE_POSITION_PCT*100:.0f}%", f"{MAX_TOTAL_EXPOSURE_PCT*100:.0f}%",
                  str(MAX_CONCURRENT_POSITIONS), f"{MAX_DAILY_LOSS_PCT*100:.1f}%",
                  f"{MAX_DRAWDOWN_PCT*100:.0f}%", "3 days"],
    "Current": [
        "OK",
        f"{len(positions) if 'positions' in dir() and positions else 0} positions",
        f"{len(positions) if 'positions' in dir() and positions else 0}/{MAX_CONCURRENT_POSITIONS}",
        "Monitoring",
        f"{drawdown*100:.1f}%",
        pause or "Not paused",
    ],
}
st.dataframe(pd.DataFrame(risk_data), use_container_width=True)


# --- RECENT RUNS ---
st.header("Recent Agent Runs")

runs_path = PROJECT_ROOT / "agent" / "equity_runs.jsonl"
if runs_path.exists():
    runs = []
    for line in runs_path.read_text().strip().split("\n"):
        if line:
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if runs:
        runs_df = pd.DataFrame(runs[-20:])  # Last 20 runs
        display_cols = [c for c in ["timestamp", "mode", "proposals", "approved",
                                     "executed", "total_deployed", "portfolio_value"]
                        if c in runs_df.columns]
        runs_df["timestamp"] = pd.to_datetime(runs_df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(runs_df[display_cols], use_container_width=True)
    else:
        st.info("No runs recorded yet")
else:
    st.info("No runs recorded yet")


# --- FOOTER ---
st.divider()
st.caption("This is a paper trading dashboard. Not financial advice. All trading decisions are yours.")
