"""Login LDC + ir a Aplicaciones + volcar la estructura del formulario
(mat-selects, inputs, botones) para poder automatizar la búsqueda."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".ldc_profile"
USER, PWD = "<EXTRANET_USER_EMAIL>", "<PASSWORD>"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
        viewport={"width":1500,"height":950}, args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(7000)
    # login si hace falta
    if "login" in page.url.lower() or page.locator("input[type=password]").count() > 0:
        print("[+] login...")
        try:
            em = page.locator("input[type=email], input[name*=user i], input[type=text]").first
            em.fill(USER)
            page.locator("input[type=password]").first.fill(PWD)
            for s in ["button:has-text('Iniciar')","button[type=submit]","button:has-text('sesión')"]:
                if page.locator(s).count()>0: page.locator(s).first.click(); break
            page.wait_for_timeout(7000)
        except Exception as e: print("login err", str(e)[:80])
    print("URL:", page.url)
    # ir a Aplicaciones
    for s in ["text=Aplicaciones","a:has-text('Aplicaciones')","[role=tab]:has-text('Aplicaciones')"]:
        try:
            if page.locator(s).count()>0: page.locator(s).first.click(timeout=4000); break
        except Exception: pass
    page.wait_for_timeout(4000)
    print("\n=== mat-select / select ===")
    info = page.evaluate("""() => {
        const out=[];
        document.querySelectorAll('mat-select, select, [role=combobox]').forEach(e=>{
            out.push({tag:e.tagName, aria:e.getAttribute('aria-label'), fcn:e.getAttribute('formcontrolname'),
                      id:e.id, placeholder:e.getAttribute('placeholder'), text:(e.innerText||'').slice(0,30)});
        });
        return out;
    }""")
    for x in info: print("  ", x)
    print("\n=== inputs (fecha/texto) ===")
    inp = page.evaluate("""() => Array.from(document.querySelectorAll('input')).slice(0,25).map(e=>(
        {type:e.type, name:e.name, fcn:e.getAttribute('formcontrolname'), ph:e.placeholder, id:e.id}))""")
    for x in inp: print("  ", x)
    print("\n=== labels visibles ===")
    labs = page.evaluate("""() => Array.from(document.querySelectorAll('mat-label, label')).slice(0,25).map(e=>(e.innerText||'').trim()).filter(Boolean)""")
    print("  ", labs)
    print("\n=== botones ===")
    btns = page.evaluate("""() => Array.from(document.querySelectorAll('button')).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,20)""")
    print("  ", btns)
    ctx.close()
