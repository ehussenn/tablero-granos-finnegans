import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto("https://www.acabase.com.ar/pcoop.asp",wait_until="domcontentloaded"); page.wait_for_timeout(2500)
    items=page.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick]')).map(e=>({
        t:(e.innerText||'').trim(), h:(e.getAttribute('href')||''), oc:(e.getAttribute('onclick')||'').slice(0,70)
    })).filter(x=>x.t&&x.t.length<45&&(x.h||x.oc))""")
    seen=set()
    for it in items:
        k=it["t"]
        if k in seen: continue
        seen.add(k)
        print(f"  {it['t'][:34]:34} | {it['h'][:48]} {('OC:'+it['oc']) if it['oc'] else ''}")
    b.close()
