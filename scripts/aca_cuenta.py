import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    fr=next((f for f in page.frames if "cuentas_datos" in f.url), None)
    href=None
    if fr:
        href=fr.evaluate("""()=>{const a=document.querySelector('a[href*=crea_varsession]'); return a?a.getAttribute('href'):null;}""")
    print("href cuenta:",href)
    if href:
        full="https://www.acabase.com.ar/consulaco/"+href
        page.goto(full,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(4000)
    print("URL:",page.url)
    for f in page.frames:
        try: txt=f.evaluate("()=>document.body?document.body.innerText.replace(/\s+/g,' ').trim().slice(0,150):''")
        except: txt=""
        if not txt: continue
        links=f.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick],option')).map(e=>({t:(e.innerText||e.value||'').trim().slice(0,32),h:(e.getAttribute('href')||'').slice(0,45),oc:(e.getAttribute('onclick')||'').slice(0,45)})).filter(x=>x.t&&x.t.length<34&&(x.h||x.oc)).slice(0,30)""")
        print("FRAME:",f.url[-38:],"|",txt[:110])
        for l in links: print("   •",l["t"],"|",l["h"]or l["oc"])
    b.close()
