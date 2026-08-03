import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
USER=sys.argv[1] if len(sys.argv)>1 else "agronasaja"
PWD =sys.argv[2] if len(sys.argv)>2 else "nasaja12345"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    if "acabase" not in page.url: page.goto("https://www.acabase.com.ar/",wait_until="domcontentloaded"); page.wait_for_timeout(2000)
    page.evaluate("""(c)=>{const u=document.getElementById('usuario'),p=document.getElementById('password');
        const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        set.call(u,c.u); set.call(p,c.p);
        u.dispatchEvent(new Event('change',{bubbles:true})); p.dispatchEvent(new Event('change',{bubbles:true}));}""",{"u":USER,"p":PWD})
    page.wait_for_timeout(400)
    try:
        with page.expect_navigation(timeout=15000):
            page.evaluate("()=>document.getElementById('login').submit()")
    except Exception as e: print("nav:",str(e)[:60])
    page.wait_for_timeout(3000)
    print("despues:",page.url)
    print("body:", page.evaluate("()=>document.body.innerText.slice(0,250)").replace(chr(10)," "))
    links=page.evaluate("""()=>Array.from(document.querySelectorAll('a')).map(a=>(a.innerText||'').trim()).filter(t=>t&&t.length<35).slice(0,40)""")
    print("links:",links[:30])
    b.close()
