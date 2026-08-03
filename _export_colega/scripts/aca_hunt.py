import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
KW=re.compile(r"entrega|liquidac|analis|calid|certific|carta|porte|romaneo|merma|disponib",re.I)
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    for f in page.frames:
        try:
            acts=f.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick],area,option')).map(e=>({
                t:(e.innerText||e.value||e.textContent||'').trim().slice(0,35),
                h:(e.getAttribute('href')||'').slice(0,60), oc:(e.getAttribute('onclick')||'').slice(0,60)
            })).filter(x=>x.t||x.h||x.oc)""")
        except: continue
        hits=[a for a in acts if KW.search(a["t"]+" "+a["h"]+" "+a["oc"])]
        if hits:
            print("=== frame:", f.url[-55:])
            for h in hits[:20]: print("   •",h["t"],"|",h["h"],"|",h["oc"])
    b.close()
