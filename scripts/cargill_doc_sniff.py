"""Sniff del endpoint de descarga de documentos (liquidación) de Cargill, y bajar
uno para ver si la calidad sale en el PDF/texto."""
import sys, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
reqs = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
        viewport={"width":1500,"height":950}, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", lambda r: reqs.append((r.method, r.url)) if "cglcloud.com" in r.url else None)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Documents", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(9000)
    if "/login" in page.url: print("[X] sesion vencida"); ctx.close(); sys.exit(1)
    # intentar clickear el primer botón/ícono de descarga
    clicked=False
    for sel in ["button[title*=ownload]","a[title*=ownload]","[class*=download]","button:has-text('Descargar')",
                "mat-icon:has-text('download')","[aria-label*=escargar]","svg[class*=download]"]:
        try:
            el=page.locator(sel).first
            if el.count()>0:
                try:
                    with page.expect_download(timeout=15000) as di:
                        el.click(timeout=5000)
                    d=di.value; fp=OUT/("cargill_liq_"+(d.suggested_filename or 'doc.pdf'))
                    d.save_as(str(fp)); print(f"[+] descarga OK: {fp} via {sel}"); clicked=True; break
                except Exception:
                    el.click(timeout=4000); clicked=True; print(f"[+] click (sin download event) via {sel}"); break
        except Exception: pass
    page.wait_for_timeout(6000)
    ctx.close()
print("\n=== requests a cglcloud con 'doc'/'download'/'pdf' ===")
seen=set()
for m,u in reqs:
    b=u.split('?')[0]
    if any(w in u.lower() for w in ['doc','download','pdf','file','attach']) and b not in seen:
        seen.add(b); print(f"  {m} {u[:130]}")
print(f"[+] total reqs cglcloud: {len(reqs)}")
print("[+] PDFs en out:", [f.name for f in OUT.glob('cargill_liq_*')])
