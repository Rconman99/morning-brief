"""Live trading dashboard — web server that shows real-time portfolio status.

Run: .venv/bin/python agent/dashboard.py
Open: http://localhost:8080 (local) or http://VPS_IP:8080 (remote)

Auto-refreshes every 30 seconds. No dependencies beyond stdlib + requests.
"""

import json
import os
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

# Load env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def get_wallet():
    # Allow read-only viewing via address override (e.g. view bot wallet without holding its key)
    addr = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if addr:
        return addr
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not key:
        return ""
    from eth_account import Account
    return Account.from_key(key).address


def get_positions(wallet):
    try:
        r = requests.get(f"https://data-api.polymarket.com/positions?user={wallet}&sizeThreshold=0.01", timeout=15)
        return r.json() if r.status_code == 200 else []
    except:
        return []


def get_activity(wallet, limit=20):
    try:
        r = requests.get(f"https://data-api.polymarket.com/activity?user={wallet}&limit={limit}", timeout=15)
        return r.json() if r.status_code == 200 else []
    except:
        return []


def get_open_orders():
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not key:
        return []
    try:
        from py_clob_client_v2 import ClobClient
        boot = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=key,
            signature_type=0,
        )
        creds = boot.create_or_derive_api_key()
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=key,
            creds=creds,
            signature_type=0,
        )
        orders = client.get_open_orders()
        return orders if isinstance(orders, list) else []
    except:
        return []


def get_run_history():
    log = PROJECT_ROOT / "agent" / "runs.jsonl"
    if not log.exists():
        return []
    runs = []
    for line in log.read_text().strip().split("\n"):
        if line:
            try:
                runs.append(json.loads(line))
            except:
                pass
    return runs[-20:]


def build_dashboard_data(wallet):
    positions = get_positions(wallet)
    activity = get_activity(wallet)
    orders = get_open_orders()
    runs = get_run_history()

    active = [p for p in positions if (p.get("currentValue") or 0) > 0]
    resolved = [p for p in positions if (p.get("currentValue") or 0) == 0]
    wins = [p for p in resolved if (p.get("cashPnl") or 0) > 0]
    losses = [p for p in resolved if (p.get("cashPnl") or 0) < 0]

    total_invested = sum(p.get("initialValue", 0) for p in positions)
    total_current = sum(p.get("currentValue", 0) for p in positions)
    total_pnl = total_current - total_invested
    total_realized = sum(p.get("realizedPnl", 0) for p in positions)
    win_rate = len(wins) / len(resolved) * 100 if resolved else 0

    return {
        "wallet": wallet,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "active": active,
        "resolved": resolved,
        "orders": orders,
        "activity": activity[:10],
        "runs": runs,
        "stats": {
            "active_count": len(active),
            "resolved_count": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_invested": total_invested,
            "total_current": total_current,
            "unrealized_pnl": total_pnl,
            "realized_pnl": total_realized,
            "open_orders": len(orders),
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>Polymarket Agent Dashboard</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0a0f; color: #e0e0e0; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 14px; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
h1 {{ color: #00ff88; font-size: 24px; margin-bottom: 5px; }}
.subtitle {{ color: #666; font-size: 12px; margin-bottom: 20px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }}
.card {{ background: #12121a; border: 1px solid #1e1e2e; border-radius: 8px; padding: 15px; }}
.card-label {{ color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }}
.card-value {{ font-size: 28px; font-weight: bold; margin-top: 5px; }}
.green {{ color: #00ff88; }}
.red {{ color: #ff4444; }}
.yellow {{ color: #ffaa00; }}
.blue {{ color: #4488ff; }}
.section {{ margin-bottom: 25px; }}
.section-title {{ color: #888; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; border-bottom: 1px solid #1e1e2e; padding-bottom: 5px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; color: #555; font-size: 11px; text-transform: uppercase; padding: 8px 10px; border-bottom: 1px solid #1e1e2e; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #0e0e16; font-size: 13px; }}
tr:hover {{ background: #15151f; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
.badge-live {{ background: #002200; color: #00ff88; border: 1px solid #00ff88; }}
.badge-filled {{ background: #001133; color: #4488ff; border: 1px solid #4488ff; }}
.badge-win {{ background: #002200; color: #00ff88; }}
.badge-loss {{ background: #220000; color: #ff4444; }}
.pulse {{ animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
.footer {{ color: #333; font-size: 11px; text-align: center; margin-top: 30px; }}
</style>
</head>
<body>
<div class="container">
<h1>POLYMARKET AGENT <span class="pulse green">LIVE</span></h1>
<div class="subtitle">Wallet: {wallet} | Updated: {timestamp} | Auto-refresh: 30s</div>

<div class="grid">
<div class="card">
<div class="card-label">Portfolio Value</div>
<div class="card-value blue">${total_current:.2f}</div>
</div>
<div class="card">
<div class="card-label">Unrealized P&L</div>
<div class="card-value {pnl_class}">{pnl_sign}${abs_pnl:.2f}</div>
</div>
<div class="card">
<div class="card-label">Realized P&L</div>
<div class="card-value {rpnl_class}">{rpnl_sign}${abs_rpnl:.2f}</div>
</div>
<div class="card">
<div class="card-label">Win Rate</div>
<div class="card-value {wr_class}">{win_rate:.0f}%</div>
</div>
<div class="card">
<div class="card-label">Active / Resolved</div>
<div class="card-value">{active_count} / {resolved_count}</div>
</div>
<div class="card">
<div class="card-label">Open Orders</div>
<div class="card-value yellow">{open_orders}</div>
</div>
</div>

{orders_section}

<div class="section">
<div class="section-title">Active Positions</div>
{positions_table}
</div>

{resolved_section}

<div class="section">
<div class="section-title">Recent Trades</div>
{activity_table}
</div>

<div class="section">
<div class="section-title">Agent Run History</div>
{runs_table}
</div>

<div class="footer">Polymarket Agent v1.0 | Bangalore VPS | 15-min scan cycle</div>
</div>
</body>
</html>"""


def render_positions(active):
    if not active:
        return '<p style="color:#333">No active positions</p>'
    rows = ""
    for p in sorted(active, key=lambda x: x.get("currentValue", 0), reverse=True):
        title = p.get("title", "")[:50]
        outcome = p.get("outcome", "?")
        size = p.get("size", 0)
        avg = p.get("avgPrice", 0)
        cur = p.get("curPrice", 0)
        value = p.get("currentValue", 0)
        pnl = p.get("cashPnl", 0)
        pnl_pct = p.get("percentPnl", 0)
        cls = "green" if pnl >= 0 else "red"
        sign = "+" if pnl >= 0 else ""
        rows += f'<tr><td>{outcome}</td><td>{title}</td><td>{size:.1f}</td><td>{avg:.3f}</td><td>{cur:.3f}</td><td>${value:.2f}</td><td class="{cls}">{sign}${pnl:.2f} ({pnl_pct:+.1f}%)</td></tr>'
    return f'<table><tr><th>Side</th><th>Market</th><th>Shares</th><th>Avg</th><th>Now</th><th>Value</th><th>P&L</th></tr>{rows}</table>'


def render_orders(orders):
    if not orders:
        return ""
    rows = ""
    for o in orders:
        if isinstance(o, dict):
            rows += f'<tr><td>{o.get("side","?")}</td><td>{o.get("original_size", o.get("size","?"))}</td><td>{o.get("price","?")}</td><td><span class="badge badge-live">LIVE</span></td></tr>'
    return f'<div class="section"><div class="section-title">Open Orders</div><table><tr><th>Side</th><th>Size</th><th>Price</th><th>Status</th></tr>{rows}</table></div>'


def render_resolved(resolved):
    if not resolved:
        return ""
    rows = ""
    for p in sorted(resolved, key=lambda x: x.get("cashPnl", 0)):
        title = p.get("title", "")[:50]
        pnl = p.get("cashPnl", 0)
        cls = "green" if pnl >= 0 else "red"
        badge = "badge-win" if pnl >= 0 else "badge-loss"
        label = "WIN" if pnl >= 0 else "LOSS"
        rows += f'<tr><td>{title}</td><td>${p.get("initialValue",0):.2f}</td><td class="{cls}">${pnl:+.2f}</td><td><span class="badge {badge}">{label}</span></td></tr>'
    return f'<div class="section"><div class="section-title">Resolved ({len(resolved)})</div><table><tr><th>Market</th><th>Invested</th><th>P&L</th><th>Result</th></tr>{rows}</table></div>'


def render_activity(activity):
    if not activity:
        return '<p style="color:#333">No recent trades</p>'
    rows = ""
    for a in activity:
        ts = datetime.fromtimestamp(a.get("timestamp", 0)).strftime("%m/%d %H:%M")
        title = a.get("title", "")[:40]
        outcome = a.get("outcome", "")
        side = a.get("side", "?")
        price = a.get("price", 0)
        usdc = a.get("usdcSize", 0)
        rows += f'<tr><td>{ts}</td><td>{side}</td><td>{outcome}</td><td>{title}</td><td>{price:.3f}</td><td>${usdc:.2f}</td></tr>'
    return f'<table><tr><th>Time</th><th>Side</th><th>Outcome</th><th>Market</th><th>Price</th><th>USDC</th></tr>{rows}</table>'


def render_runs(runs):
    if not runs:
        return '<p style="color:#333">No runs yet</p>'
    rows = ""
    for r in reversed(runs[-10:]):
        ts = r.get("timestamp", "")[:16]
        mode = r.get("mode", "?")
        proposals = r.get("proposals", 0)
        approved = r.get("approved", 0)
        executed = r.get("executed", 0)
        deployed = r.get("total_deployed", 0)
        rows += f'<tr><td>{ts}</td><td>{mode}</td><td>{proposals}</td><td>{approved}</td><td>{executed}</td><td>${deployed:.2f}</td></tr>'
    return f'<table><tr><th>Time</th><th>Mode</th><th>Found</th><th>Approved</th><th>Executed</th><th>Deployed</th></tr>{rows}</table>'


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            wallet = get_wallet()
            data = build_dashboard_data(wallet) if wallet else {}
            self.wfile.write(json.dumps(data, default=str).encode())
            return

        wallet = get_wallet()
        if not wallet:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"No POLYMARKET_PRIVATE_KEY in .env")
            return

        data = build_dashboard_data(wallet)
        s = data["stats"]

        pnl = s["unrealized_pnl"]
        rpnl = s["realized_pnl"]

        html = HTML_TEMPLATE.format(
            wallet=wallet[:8] + "..." + wallet[-6:],
            timestamp=data["timestamp"],
            total_current=s["total_current"],
            pnl_class="green" if pnl >= 0 else "red",
            pnl_sign="+" if pnl >= 0 else "-",
            abs_pnl=abs(pnl),
            rpnl_class="green" if rpnl >= 0 else "red",
            rpnl_sign="+" if rpnl >= 0 else "-",
            abs_rpnl=abs(rpnl),
            win_rate=s["win_rate"],
            wr_class="green" if s["win_rate"] >= 50 else "red" if s["win_rate"] > 0 else "blue",
            active_count=s["active_count"],
            resolved_count=s["resolved_count"],
            open_orders=s["open_orders"],
            orders_section=render_orders(data["orders"]),
            positions_table=render_positions(data["active"]),
            resolved_section=render_resolved(data["resolved"]),
            activity_table=render_activity(data["activity"]),
            runs_table=render_runs(data["runs"]),
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Dashboard live at http://localhost:{port}")
    print(f"API endpoint: http://localhost:{port}/api/data")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard")
        server.server_close()


if __name__ == "__main__":
    main()
