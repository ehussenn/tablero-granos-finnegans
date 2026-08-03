import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    fr=next((f for f in page.frames if "resumen.asp" in f.url), page.frames[-1])
    items=fr.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick],img,input,button')).map(e=>({
        t:(e.innerText||e.value||e.alt||e.title||'').trim().slice(0,30),
        h:(e.getAttribute('href')||'').slice(0,60), oc:(e.getAttribute('onclick')||'').slice(0,60)
    })).filter(x=>(x.h||x.oc))""")
    seen=set()
    for it in items:
        k=it["t"]+it["h"]+it["oc"]
        if k in seen: continue
        seen.add(k)
        print("  •",it["t"],"| h:",it["h"],"| oc:",it["oc"])
    b.close()
