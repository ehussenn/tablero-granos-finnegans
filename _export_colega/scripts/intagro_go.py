import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"intagro_nav"; OUT.mkdir(parents=True,exist_ok=True)
route = sys.argv[1] if len(sys.argv)>1 else "analisis"
n=[0]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]
    print("pestañas:", [pg.url for pg in ctx.pages])
    # elegir la pestaña del portal (no dashboard de mensajes)
    page=None
    for pg in ctx.pages:
        if "portal.intagro.com" in pg.url and "dashboard" not in pg.url: page=pg; break
    if not page: page=ctx.pages[-1]
    def on_resp(r):
        try:
            ct=r.headers.get("content-type",""); u=r.url
            if "json" not in ct: return
            body=r.text()
            if len(body)<20: return
            n[0]+=1
            safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:50]
            (OUT/f"{n[0]:03d}_{safe}.json").write_text(body[:500000],encoding="utf-8")
        except: pass
    page.on("response", on_resp)
    page.goto(f"https://portal.intagro.com/{route}/?area=GV", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4500)
    print("URL:", page.url)
    info=page.evaluate("""()=>({
        inputs:Array.from(document.querySelectorAll('input,select')).map(e=>({id:e.id,type:e.type,ph:e.placeholder})).filter(x=>x.type!=='hidden').slice(0,12),
        thead:Array.from(document.querySelectorAll('th')).map(e=>e.innerText.trim()).filter(Boolean).slice(0,25),
        btns:Array.from(document.querySelectorAll('a,button')).map(e=>(e.innerText||'').trim()).filter(t=>/export|imprimir|aplicar/i.test(t)).slice(0,6)
    })""")
    print("inputs:", info["inputs"])
    print("thead:", info["thead"])
    print("btns:", info["btns"])
    b.close()
