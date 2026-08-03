import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    # click la primera cuenta
    fr=next((f for f in page.frames if "cuentas_datos" in f.url), None)
    if fr:
        try: fr.locator("a").first.click(timeout=5000)
        except Exception as e: print("click cuenta:",str(e)[:50])
    page.wait_for_timeout(4000)
    print("URL:",page.url)
    for f in page.frames:
        try: txt=f.evaluate("()=>document.body?document.body.innerText.replace(/\s+/g,' ').trim().slice(0,160):''")
        except: txt=""
        if not txt: continue
        links=f.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick]')).map(e=>({t:(e.innerText||'').trim().slice(0,30),h:(e.getAttribute('href')||'').slice(0,45),oc:(e.getAttribute('onclick')||'').slice(0,45)})).filter(x=>x.t&&x.t.length<32&&(x.h||x.oc)).slice(0,25)""")
        print("FRAME:",f.url[-40:],"|",txt[:120])
        for l in links: print("   •",l["t"],"|",l["h"]or l["oc"])
    b.close()
