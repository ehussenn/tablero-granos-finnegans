import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DL=ROOT/"scraper"/"out"/"intagro_dl"; DL.mkdir(parents=True,exist_ok=True)
desde=sys.argv[1] if len(sys.argv)>1 else "2023-10-27"
hasta=sys.argv[2] if len(sys.argv)>2 else "2026-06-26"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]
    page=next((pg for pg in ctx.pages if "analisis" in pg.url), ctx.pages[-1])
    if "analisis" not in page.url:
        page.goto("https://portal.intagro.com/analisis/?area=GV",wait_until="domcontentloaded"); page.wait_for_timeout(4000)
    # set date inputs (type=date)
    r=page.evaluate("""(o)=>{
        const ds=Array.from(document.querySelectorAll('input[type=date]'));
        if(ds[0]){ds[0].value=o.d; ds[0].dispatchEvent(new Event('input',{bubbles:true})); ds[0].dispatchEvent(new Event('change',{bubbles:true}));}
        if(ds[1]){ds[1].value=o.h; ds[1].dispatchEvent(new Event('input',{bubbles:true})); ds[1].dispatchEvent(new Event('change',{bubbles:true}));}
        return 'dates='+ds.length;
    }""",{"d":desde,"h":hasta})
    print("[+]",r)
    try:
        page.locator("text=Aplicar Filtro").first.click(timeout=5000); page.wait_for_timeout(4000)
        print("[+] filtro aplicado")
    except Exception as e: print("[!] aplicar:",str(e)[:60])
    # cuántas filas hay
    nrows=page.evaluate("()=>document.querySelectorAll('tbody tr').length")
    print("[+] filas en tabla:", nrows)
    try:
        with page.expect_download(timeout=40000) as di:
            page.locator("text=Exportar").first.click()
        d=di.value; fp=str(DL/d.suggested_filename); d.save_as(fp)
        print("[+] EXPORT:", fp)
    except Exception as e: print("[!] export:", str(e)[:90])
    b.close()
