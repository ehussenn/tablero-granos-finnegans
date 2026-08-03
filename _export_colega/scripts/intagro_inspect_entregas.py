import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(4500)
    info=page.evaluate("""()=>{
        const dates=Array.from(document.querySelectorAll('input[type=date]')).map(e=>({id:e.id,name:e.name,val:e.value,vis:e.offsetParent!==null}));
        const apl=Array.from(document.querySelectorAll('*')).filter(e=>/aplicar filtro/i.test((e.innerText||'').trim()) && e.children.length<=1).map(e=>({tag:e.tagName,id:e.id,cls:(e.className||'').slice(0,40),txt:(e.innerText||'').trim().slice(0,25)}));
        const exp=Array.from(document.querySelectorAll('*')).filter(e=>/exportar/i.test((e.innerText||'').trim()) && e.children.length<=1).map(e=>({tag:e.tagName,id:e.id,cls:(e.className||'').slice(0,30)}));
        return {dates,apl,exp};
    }""")
    print("date inputs:", info["dates"])
    print("APLICAR:", info["apl"])
    print("EXPORTAR:", info["exp"])
    b.close()
