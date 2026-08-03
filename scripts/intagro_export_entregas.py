import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DL=ROOT/"scraper"/"out"/"intagro_dl"; DL.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(4500)
    print("URL:",page.url)
    # rango amplio
    page.evaluate("""()=>{const ds=Array.from(document.querySelectorAll('input[type=date]'));
        if(ds[0]){ds[0].value='2023-10-27';ds[0].dispatchEvent(new Event('change',{bubbles:true}));}
        if(ds[1]){ds[1].value='2026-06-26';ds[1].dispatchEvent(new Event('change',{bubbles:true}));}}""")
    for sel in ["button:has-text('Aplicar')","text=Aplicar Filtro","a:has-text('Aplicar')"]:
        try:
            if page.locator(sel).count()>0: page.locator(sel).first.click(timeout=4000); break
        except: pass
    page.wait_for_timeout(4000)
    print("filas:", page.evaluate("()=>document.querySelectorAll('tbody tr').length"))
    try:
        with page.expect_download(timeout=40000) as di:
            page.locator("text=Exportar").first.click()
        d=di.value; fp=str(DL/("entregas_"+d.suggested_filename)); d.save_as(fp)
        print("[+] EXPORT:", fp)
    except Exception as e: print("[!] export:",str(e)[:90])
    b.close()
