import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DL=ROOT/"scraper"/"out"/"intagro_dl"; DL.mkdir(parents=True,exist_ok=True)
route=sys.argv[1] if len(sys.argv)>1 else "entregas"
desde=sys.argv[2] if len(sys.argv)>2 else "2025-09-01"
hasta=sys.argv[3] if len(sys.argv)>3 else "2026-06-26"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto(f"https://portal.intagro.com/{route}/?area=GV",wait_until="domcontentloaded",timeout=60000)
    page.wait_for_timeout(4500)
    r=page.evaluate("""(o)=>{
        function set(id,v){const e=document.getElementById(id); if(!e)return false;
            const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(e,v);
            e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); return e.value;}
        return {d:set('desde',o.d), h:set('hasta',o.h)};
    }""",{"d":desde,"h":hasta})
    print("[+] fechas:",r)
    try:
        page.locator("a:has-text('Aplicar Filtro')").first.click(timeout=6000); print("[+] aplicar OK")
    except Exception as e: print("[!] aplicar:",str(e)[:60])
    page.wait_for_timeout(6000)
    print("[+] filas:", page.evaluate("()=>document.querySelectorAll('tbody tr').length"))
    try:
        with page.expect_download(timeout=45000) as di:
            page.locator("a:has-text('Exportar'), text=Exportar").first.click()
        d=di.value; fp=str(DL/(route+"_v3_"+d.suggested_filename)); d.save_as(fp)
        print("[+] EXPORT:", fp)
    except Exception as e: print("[!] export:",str(e)[:90])
    b.close()
