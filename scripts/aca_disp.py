import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    for f in page.frames:
        try: txt=f.evaluate("()=>document.body?document.body.innerText.replace(/\s+/g,' ').trim().slice(0,200):''")
        except: txt=""
        if not txt: continue
        heads=f.evaluate("""()=>Array.from(document.querySelectorAll('th')).map(t=>t.innerText.trim()).filter(Boolean).slice(0,16)""")
        links=f.evaluate("""()=>Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim().slice(0,30),h:(a.getAttribute('href')||'').slice(0,40)})).filter(x=>x.t&&x.t.length<32).slice(0,18)""")
        print("FRAME:",f.url[-45:]); print("  TXT:",txt[:180])
        if heads: print("  HEADS:",heads)
        for l in links: 
            if l["h"]: print("   link:",l["t"],"|",l["h"])
    b.close()
