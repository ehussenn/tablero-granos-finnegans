"""Allaria: login + navegar a cada página /clientes/*.asp dentro de la MISMA sesión Playwright."""
from __future__ import annotations
import sys, os, json, time, re
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".allaria_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "allaria"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("ALLARIA_USER", "AGRONASAJA")
PWD = os.environ.get("ALLARIA_PASS", "Agronasaja.1234")

PAGES = [
    ("clientes_per", "Resumen"),
    ("clientes_fis", "Físico (mercaderías)"),
    ("clientes_des_pend", "Descargas pendientes"),
    ("clientes_btm", "Btm"),
    ("clientes_dol", "Dólares"),
    ("boletos", "Boletos"),
    ("clientes_ter", "Terminados"),
    ("clientes_cta", "Cuenta corriente"),
    ("clientes_din", "Dinero"),
]

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Login
    print("[+] Login...")
    page.goto("https://www.allariaagro.com.ar/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    try:
        page.locator("a:has-text('Login')").first.click(timeout=4000)
        page.wait_for_timeout(3000)
    except: pass
    try:
        page.locator("input[type='text']").first.fill(USER, timeout=4000)
        page.locator("input[type='password']").first.fill(PWD, timeout=4000)
        page.locator("button[type='submit']").first.click(timeout=4000)
        page.wait_for_timeout(15000)
    except Exception as e: print(f"  [!] login: {e}")
    print(f"[+] URL post-login: {page.url}")

    # Visitar cada página
    for pg, label in PAGES:
        url = f"https://clientes.allariaagro.com.ar/clientes/{pg}.asp"
        print(f"\n[+] Goto {pg}.asp ({label})...")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            html = page.content()
            (OUT/f"page_{pg}.html").write_text(html, encoding="utf-8", errors="ignore")
            page.screenshot(path=str(OUT/f"page_{pg}.png"), full_page=True)
            # Analisis rapido
            ntbl = len(re.findall(r'<table', html, re.IGNORECASE))
            ctg_matches = re.findall(r'\b\d{11}\b', html)
            ctg_unique = sorted(set(ctg_matches))
            body_text = page.inner_text("body", timeout=3000)[:500] if html else ""
            print(f"   {len(html)} bytes  tables={ntbl}  CTGs unicos={len(ctg_unique)}")
            if ctg_unique[:5]: print(f"   sample CTGs: {ctg_unique[:5]}")
            if "Error" in body_text or "no autoriz" in body_text.lower() or "denegado" in body_text.lower():
                print(f"   [!] Error/denegado en body: {body_text[:200]}")
        except Exception as e:
            print(f"   [!] err: {str(e)[:80]}")
        time.sleep(1)

    print("\n[+] Done.")
    page.wait_for_timeout(5000)
    ctx.close()
