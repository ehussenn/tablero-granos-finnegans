"""Attach por CDP a la sesión Finnegans (:9340) y captura respuestas JSON con señales
de liquidación/CTG mientras el usuario navega. Guarda en scraper/out/finn_sniff."""
import sys, re, json
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"finn_sniff"; OUT.mkdir(parents=True,exist_ok=True)
for f in OUT.glob("*"):
    try: f.unlink()
    except: pass
LOG=OUT/"_log.txt"; LOG.write_text("",encoding="utf-8")
KW=["liquidac","carta","porte","ctg","coe","comprobante","entrega","traslado","numerodocumentoadicional","romaneo"]
n=[0]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    ctx=b.contexts[0]
    def hook(page):
        def on_resp(r):
            try:
                u=r.url; ct=r.headers.get("content-type","")
                if any(s in u for s in [".js",".css",".woff",".png",".svg",".ico",".gif",".jpg"]): return
                body=r.text()
                if len(body)<15: return
                low=body.lower()
                if "json" not in ct and sum(low.count(k) for k in KW)<3: return
                n[0]+=1
                safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:55]
                (OUT/f"{n[0]:03d}_{safe}.txt").write_text(body[:400000],encoding="utf-8")
                with open(LOG,"a",encoding="utf-8") as f:
                    f.write(f"{n[0]:03d} {r.request.method} {u[:110]} | body={r.request.post_data if r.request.post_data else ''}\n"[:400])
            except: pass
        page.on("response", on_resp)
    for pg in ctx.pages: hook(pg)
    ctx.on("page", hook)
    print("[+] Sniffer atado a Finnegans. Navegá a un contrato de VENTA -> Liquidaciones.", flush=True)
    print("[+] Guardo todo en", OUT, flush=True)
    try: ctx.wait_for_event("close", timeout=0)
    except Exception: pass
