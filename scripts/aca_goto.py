import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"aca_nav"; OUT.mkdir(parents=True,exist_ok=True)
url=sys.argv[1] if len(sys.argv)>1 else "https://www.acabase.com.ar/ACAbase_Dir/analizacuenta.asp?xcontrol=9"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto(url,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(3500)
    print("URL:",page.url)
    # frames
    print("frames:",[f.url[:70] for f in page.frames])
    # contenido principal: links + texto + selects
    info=page.evaluate("""()=>({
        txt:(document.body.innerText||'').replace(/\s+/g,' ').slice(0,400),
        links:Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim(),h:(a.getAttribute('href')||'').slice(0,50)})).filter(x=>x.t&&x.t.length<45).slice(0,30),
        selects:Array.from(document.querySelectorAll('select')).map(s=>({id:s.id,n:s.name,opts:Array.from(s.options).slice(0,8).map(o=>o.text.trim())}))
    })""")
    print("TXT:",info["txt"][:300])
    print("LINKS:"); [print("  ",l["t"],"|",l["h"]) for l in info["links"]]
    print("SELECTS:",info["selects"])
    b.close()
