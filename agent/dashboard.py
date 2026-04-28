"""Dashboard v2"""
import json,os,sys,requests
from datetime import datetime
from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from string import Template
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
for l in (ROOT/".env").read_text().strip().split("\n") if (ROOT/".env").exists() else []:
    if l.strip() and not l.startswith("#") and "=" in l:
        k,v=l.split("=",1);os.environ.setdefault(k.strip(),v.strip())
def wallet():
    from eth_account import Account
    return Account.from_key(os.environ.get("POLYMARKET_PRIVATE_KEY","")).address
def api_get(u):
    try:return requests.get(u,timeout=15).json()
    except:return []
PAGE=Template("""<!DOCTYPE html><html><head><meta charset=UTF-8><meta name=viewport content="width=device-width,initial-scale=1"><meta http-equiv=refresh content=30><title>Polymarket Agent</title><style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0a0f;color:#e0e0e0;font-family:monospace;font-size:13px}.c{max-width:1100px;margin:0 auto;padding:16px}h1{color:#00ff88;font-size:20px}.sub{color:#444;font-size:11px;margin-bottom:16px}.row{display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap}.k{background:#111118;border:1px solid #1a1a2a;border-radius:6px;padding:10px 14px;flex:1;min-width:130px}.kl{color:#555;font-size:10px;text-transform:uppercase}.kv{font-size:22px;font-weight:700;margin-top:2px}.gr{color:#00ff88}.rd{color:#ff4444}.yl{color:#ffaa00}.bl{color:#4488ff}.s{margin-bottom:14px}.st{color:#555;font-size:11px;text-transform:uppercase;border-bottom:1px solid #1a1a2a;padding-bottom:4px;margin-bottom:6px}table{width:100%;border-collapse:collapse}th{text-align:left;color:#333;font-size:10px;text-transform:uppercase;padding:5px 8px;border-bottom:1px solid #1a1a2a}td{padding:5px 8px;border-bottom:1px solid #0e0e14;font-size:12px}tr:hover{background:#13131d}.f{color:#222;font-size:10px;text-align:center;margin-top:20px}</style></head><body><div class=c><h1>POLYMARKET AGENT <span style="color:#00ff88;font-size:11px">LIVE</span></h1><div class=sub>$wallet | $time | auto-refresh 30s</div><div class=row><div class=k><div class=kl>Value</div><div class="kv bl">$$$value</div></div><div class=k><div class=kl>P&L</div><div class="kv $pcl">$pnl</div></div><div class=k><div class=kl>Realized</div><div class="kv $rcl">$rpnl</div></div><div class=k><div class=kl>Win Rate</div><div class="kv $wcl">$wr%</div></div><div class=k><div class=kl>Orders</div><div class="kv yl">$oo</div></div></div>$orders<div class=s><div class=st>Active Positions</div>$positions</div>$resolved<div class=s><div class=st>Recent Trades</div>$trades</div><div class=f>Polymarket Agent v2 | Ensemble: Gemma+Nemotron | Kelly | Bayesian</div></div></body></html>""")
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        w=wallet()
        pos=api_get(f"https://data-api.polymarket.com/positions?user={w}&sizeThreshold=0.01")
        act=api_get(f"https://data-api.polymarket.com/activity?user={w}&limit=10")
        active=[p for p in pos if(p.get("currentValue")or 0)>0]
        resolved=[p for p in pos if(p.get("currentValue")or 0)==0]
        wins=[p for p in resolved if(p.get("cashPnl")or 0)>0]
        losses=[p for p in resolved if(p.get("cashPnl")or 0)<0]
        ti=sum(p.get("initialValue",0)for p in pos);tc=sum(p.get("currentValue",0)for p in pos)
        pnl=tc-ti;rpnl=sum(p.get("realizedPnl",0)for p in pos)
        wr=len(wins)/len(resolved)*100 if resolved else 0
        pt=""
        if active:
            pt="<table><tr><th>Side</th><th>Market</th><th>Shares</th><th>Entry</th><th>Now</th><th>Value</th><th>P&L</th></tr>"
            for p in sorted(active,key=lambda x:x.get("currentValue",0),reverse=True):
                cp=p.get("cashPnl",0);cl="gr" if cp>=0 else "rd"
                pt+=f'<tr><td>{p.get("outcome","?")}</td><td>{p.get("title","")[:40]}</td><td>{p.get("size",0):.1f}</td><td>{p.get("avgPrice",0):.3f}</td><td>{p.get("curPrice",0):.3f}</td><td>${p.get("currentValue",0):.2f}</td><td class="{cl}">{"+" if cp>=0 else ""}${cp:.2f}</td></tr>'
            pt+="</table>"
        else:pt='<div style="color:#333">No positions</div>'
        nords=0;ot=""
        try:
            from py_clob_client.client import ClobClient
            c=ClobClient("https://clob.polymarket.com",key=os.environ["POLYMARKET_PRIVATE_KEY"],chain_id=137,signature_type=0)
            c.set_api_creds(c.create_or_derive_api_creds())
            ords=c.get_orders()
            if isinstance(ords,list) and ords:
                ot='<div class="s"><div class="st">Open Orders</div><table><tr><th>Side</th><th>Size</th><th>Price</th></tr>'
                for o in ords:
                    if isinstance(o,dict):ot+=f'<tr><td>{o.get("side","?")}</td><td>{o.get("original_size",o.get("size","?"))}</td><td>{o.get("price","?")}</td></tr>'
                ot+="</table></div>"
            nords=len(ords) if isinstance(ords,list) else 0
        except:pass
        rt=""
        if resolved:
            rt=f'<div class="s"><div class="st">Resolved ({len(wins)}W/{len(losses)}L)</div><table><tr><th>Market</th><th>Invested</th><th>P&L</th></tr>'
            for p in resolved:
                cp=p.get("cashPnl",0);cl="gr" if cp>=0 else "rd"
                rt+=f'<tr><td>{p.get("title","")[:40]}</td><td>${p.get("initialValue",0):.2f}</td><td class="{cl}">{"+" if cp>=0 else ""}${cp:.2f}</td></tr>'
            rt+="</table></div>"
        tt=""
        if act:
            tt="<table><tr><th>Time</th><th>Side</th><th>Market</th><th>Price</th><th>USDC</th></tr>"
            for a in act:
                ts=datetime.fromtimestamp(a.get("timestamp",0)).strftime("%m/%d %H:%M")
                tt+=f'<tr><td>{ts}</td><td>{a.get("side","?")} {a.get("outcome","")}</td><td>{a.get("title","")[:30]}</td><td>{a.get("price",0):.3f}</td><td>${a.get("usdcSize",0):.2f}</td></tr>'
            tt+="</table>"
        html=PAGE.substitute(wallet=w[:6]+".."+w[-4:],time=datetime.utcnow().strftime("%H:%M UTC"),value=f"{tc:.2f}",pcl="gr" if pnl>=0 else "rd",pnl=f'{"+" if pnl>=0 else ""}${pnl:.2f}',rcl="gr" if rpnl>=0 else "rd",rpnl=f'{"+" if rpnl>=0 else ""}${rpnl:.2f}',wcl="gr" if wr>=50 else "bl",wr=f"{wr:.0f}",oo=nords,orders=ot,positions=pt,resolved=rt,trades=tt)
        self.send_response(200);self.send_header("Content-Type","text/html");self.end_headers();self.wfile.write(html.encode())
    def log_message(self,*a):pass
if __name__=="__main__":
    p=int(sys.argv[1]) if len(sys.argv)>1 else 8080
    HTTPServer(("0.0.0.0",p),H).serve_forever()
