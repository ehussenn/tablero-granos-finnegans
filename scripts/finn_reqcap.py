"""Captura request COMPLETO (url+body) + response de las llamadas BSA/oneteam,
recargando las tabs para re-disparar. Guarda en scraper/out/finn_req."""
import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"finn_req"; OUT.mkdir(parents=True,exist_ok=True)
for f in OUT.glob("*"):
    try: f.unlink()
    except: pass
LOG=OUT/"_req.txt"; LOG.write_text("",encoding="utf-8")
n=[0]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    ctx=b.contexts[0]
    def on_req(req):
        try:
            u=req.url
            if "finneg.com" not in u: return
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".ico",".gif"]): return
            if not any(s in u for s in ["oneteam","webreport","standardDF","/mas/","BSA","liquidac","report"]): return
            with open(LOG,"a",encoding="utf-8") as f:
                f.write(f"\n### {req.method} {u}\n")
                if req.post_data: f.write("BODY: "+req.post_data[:1500]+"\n")
        except: pass
    def on_resp(r):
        try:
            u=r.url
            if "standardDF" in u or ("webreport" in u):
                n[0]+=1
                safe=re.sub(r"[^a-z0-9]+","_",u.split("?")[0].lower())[-40:]
                (OUT/f"{n[0]:03d}_{safe}.xml").write_text(r.text()[:500000],encoding="utf-8")
        except: pass
    for pg in ctx.pages: pg.on("request",on_req); pg.on("response",on_resp)
    # recargar ambas tabs
    for pg in ctx.pages:
        try: pg.reload(wait_until="domcontentloaded",timeout=45000); pg.wait_for_timeout(6000)
        except Exception as e: print("reload:",str(e)[:60])
    print("[+] listo, requests en", LOG)
    b.close()
