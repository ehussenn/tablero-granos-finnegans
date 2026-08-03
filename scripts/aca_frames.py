import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    for f in page.frames:
        try:
            txt=f.evaluate("()=>document.body?document.body.innerText.replace(/\s+/g,' ').slice(0,300):''")
        except: txt=""
        if not txt.strip(): continue
        print("=== FRAME:", f.url[-60:])
        print("  TXT:", txt[:280])
        info=f.evaluate("""()=>({
            links:Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim(),h:(a.getAttribute('href')||'').slice(0,45),oc:(a.getAttribute('onclick')||'').slice(0,40)})).filter(x=>x.t&&x.t.length<45).slice(0,25),
            selects:Array.from(document.querySelectorAll('select')).map(s=>({id:s.id,opts:Array.from(s.options).slice(0,10).map(o=>o.text.trim())})),
            inputs:Array.from(document.querySelectorAll('input')).map(i=>({id:i.id,n:i.name,type:i.type})).filter(x=>x.type!=='hidden').slice(0,10)
        })""")
        for l in info["links"][:20]: print("   link:",l["t"],"|",l["h"],"|",l["oc"])
        if info["selects"]: print("   selects:",info["selects"])
        if info["inputs"]: print("   inputs:",info["inputs"])
    b.close()
