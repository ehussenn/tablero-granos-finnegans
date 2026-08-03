import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    # dump TODOS los frames y TODOS sus links/onclick (menu del modulo acopio)
    for f in page.frames:
        items=f.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick],img[onclick],input[type=button]')).map(e=>({
            t:(e.innerText||e.value||e.alt||e.title||'').trim().slice(0,30),
            h:(e.getAttribute('href')||'').slice(0,55), oc:(e.getAttribute('onclick')||'').slice(0,55)
        })).filter(x=>(x.h||x.oc)&&!/print\(\)|mailto|cerrarsess/.test(x.h+x.oc))""")
        if items:
            print("FRAME:",f.url[-42:])
            seen=set()
            for it in items:
                k=it["t"]+it["h"]+it["oc"]
                if k in seen: continue
                seen.add(k); print("   •",it["t"],"|",it["h"] or it["oc"])
    b.close()
