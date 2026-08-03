import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"aca_nav"; OUT.mkdir(parents=True,exist_ok=True)
cosecha=sys.argv[1] if len(sys.argv)>1 else "2526"
grano=sys.argv[2] if len(sys.argv)>2 else "210"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    # construir URL de resumen sobre la cuenta actual
    url=f"https://www.acabase.com.ar/consulaco/resumen.asp?xcosecha={cosecha}&xgrano={grano}&xctamadre=185566&xcuenta=18556602"
    page.goto(url,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(4000)
    print("URL:",page.url)
    for f in page.frames:
        try: txt=f.evaluate("()=>document.body?document.body.innerText.replace(/\s+/g,' ').trim():''")
        except: txt=""
        if not txt.strip() or len(txt)<10: continue
        heads=f.evaluate("""()=>Array.from(document.querySelectorAll('th')).map(t=>t.innerText.trim()).filter(Boolean).slice(0,18)""")
        links=f.evaluate("""()=>Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim().slice(0,28),h:(a.getAttribute('href')||'').slice(0,55)})).filter(x=>x.t&&x.h).slice(0,15)""")
        print("FRAME:",f.url[-40:]); print("  TXT:",txt[:260])
        if heads: print("  HEADS:",heads)
        for l in links: print("   •",l["t"],"|",l["h"])
        f.evaluate("()=>0")
        try: (OUT/("resumen_"+f.url.split('/')[-1][:30]+".html")).write_text(f.content(),encoding="utf-8")
        except: pass
    b.close()
