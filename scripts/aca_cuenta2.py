import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    # asegurar estar en cuentas
    if "cuentas" not in page.url and not any("cuentas_datos" in f.url for f in page.frames):
        page.goto("https://www.acabase.com.ar/consulaco/marco.asp?xllamap=cuentas_datos.asp",wait_until="domcontentloaded"); page.wait_for_timeout(3000)
    href=None
    for f in page.frames:
        try:
            h=f.evaluate("""()=>{const a=document.querySelector('a[href*=crea_varsession]'); return a?a.href:null;}""")
            if h: href=h; break
        except: pass
    print("href:",href)
    if href:
        page.goto(href,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(4500)
    print("URL:",page.url)
    for f in page.frames:
        try: txt=f.evaluate("()=>document.body?document.body.innerText.replace(/\s+/g,' ').trim():''")
        except: txt=""
        if not txt.strip(): continue
        links=f.evaluate("""()=>Array.from(document.querySelectorAll('a,[onclick],option,td')).map(e=>({t:(e.innerText||e.value||'').trim().slice(0,34),h:(e.getAttribute&&e.getAttribute('href')||'').slice(0,50),oc:(e.getAttribute&&e.getAttribute('onclick')||'').slice(0,50)})).filter(x=>x.t&&x.t.length<36&&(x.h||x.oc)).slice(0,28)""")
        print("FRAME:",f.url[-38:],"|",txt[:130])
        for l in links: print("   •",l["t"],"|",l["h"]or l["oc"])
    b.close()
