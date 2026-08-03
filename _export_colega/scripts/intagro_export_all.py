import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DL=ROOT/"scraper"/"out"/"intagro_dl"; DL.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    if "entregas" not in page.url:
        page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded"); page.wait_for_timeout(4000)
    # asegurar length 1000 + dates 25/26 + aplicar
    page.evaluate("""()=>{
        function setv(e,v){const s=Object.getOwnPropertyDescriptor(e.__proto__,'value').set; s.call(e,v); e.dispatchEvent(new Event('change',{bubbles:true})); e.dispatchEvent(new Event('input',{bubbles:true}));}
        const d=document.getElementById('desde'); if(d)setv(d,'2025-09-01');
        const h=document.getElementById('hasta'); if(h)setv(h,'2026-06-26');
        const ap=Array.from(document.querySelectorAll('a,button')).find(e=>/aplicar filtro/i.test((e.innerText||'').trim())); if(ap)ap.click();
    }""")
    page.wait_for_timeout(5000)
    page.evaluate("""()=>{const s=document.querySelector('.dataTables_length select')||document.querySelector('select'); if(s){const set=Object.getOwnPropertyDescriptor(s.__proto__,'value').set; set.call(s,'1000'); s.dispatchEvent(new Event('change',{bubbles:true}));}}""")
    page.wait_for_timeout(3000)
    print("filas:", page.evaluate("()=>document.querySelectorAll('tbody tr').length"))
    with page.expect_download(timeout=60000) as di:
        page.evaluate("""()=>{const e=Array.from(document.querySelectorAll('a,button')).find(x=>/exportar/i.test((x.innerText||'').trim())); if(e)e.click();}""")
    d=di.value; fp=str(DL/"entregas_all.xlsx"); d.save_as(fp)
    print("[+] EXPORT:", fp)
    b.close()
