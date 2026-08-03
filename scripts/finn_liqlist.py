import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"finn_liqlist"; OUT.mkdir(parents=True,exist_ok=True)
cap=[]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    ctx=b.contexts[0]; page=ctx.new_page()
    def on_resp(r):
        try:
            if "webreport/data" in r.url:
                body=r.text()
                cap.append(body)
                (OUT/f"grid_{len(cap)}.xml").write_text(body[:600000],encoding="utf-8")
        except: pass
    page.on("response",on_resp)
    page.goto("https://go.finneg.com/mas/vista?viewID=50249",wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(9000)
    # intentar click en Buscar si existe
    for sel in ["button:has-text('Buscar')","#btnBuscar","[title=Buscar]","button:has-text('Actualizar')"]:
        try:
            if page.locator(sel).count()>0: page.locator(sel).first.click(timeout=3000); page.wait_for_timeout(6000); break
        except: pass
    print("grids capturados:", len(cap))
    for i,body in enumerate(cap,1):
        liqs=len(re.findall(r"LIQ-[A-Z]+-VTA", body))
        print(f"  grid {i}: {len(body)} bytes, {liqs} liq refs")
    page.close(); b.close()
