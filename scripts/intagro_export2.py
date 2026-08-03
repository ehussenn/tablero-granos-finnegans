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
    dates=page.locator("input[type=date]")
    cnt=dates.count(); print("date inputs:",cnt)
    if cnt>=2:
        dates.nth(0).fill(desde); page.wait_for_timeout(300)
        dates.nth(1).fill(hasta); page.wait_for_timeout(300)
    # Aplicar Filtro
    clicked=False
    for sel in ["button:has-text('Aplicar Filtro')","button:has-text('Aplicar')","//button[contains(.,'Aplicar')]","//a[contains(.,'Aplicar')]"]:
        try:
            loc=page.locator(sel if not sel.startswith('//') else f"xpath={sel}")
            if loc.count()>0: loc.first.click(timeout=4000); clicked=True; print("aplicar via",sel[:30]); break
        except Exception as e: pass
    page.wait_for_timeout(5000)
    print("filas tabla:", page.evaluate("()=>document.querySelectorAll('tbody tr').length"))
    try:
        with page.expect_download(timeout=45000) as di:
            page.locator("text=Exportar").first.click()
        d=di.value; fp=str(DL/(route+"_full_"+d.suggested_filename)); d.save_as(fp)
        print("[+] EXPORT:", fp)
    except Exception as e: print("[!] export:",str(e)[:90])
    b.close()
