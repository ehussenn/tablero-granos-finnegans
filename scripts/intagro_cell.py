import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(4500)
    # aplicar filtro 25/26 para tener filas
    page.evaluate("""()=>{function setv(e,v){const s=Object.getOwnPropertyDescriptor(e.__proto__,'value').set;s.call(e,v);e.dispatchEvent(new Event('change',{bubbles:true}));}
        const d=document.getElementById('desde');if(d)setv(d,'2025-09-01');const h=document.getElementById('hasta');if(h)setv(h,'2026-06-26');
        const ap=Array.from(document.querySelectorAll('a,button')).find(e=>/aplicar filtro/i.test((e.innerText||'').trim()));if(ap)ap.click();}""")
    page.wait_for_timeout(4500)
    cell=page.evaluate("""()=>{const tr=document.querySelector('tbody tr'); if(!tr)return 'no rows';
        const td=tr.firstElementChild; return {html:td.innerHTML.slice(0,300), txt:td.innerText.trim()};}""")
    print("primera celda (Cto):", cell)
    # contratos section
    b.close()
