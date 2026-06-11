"""Allaria: visita cada /clientes/clientes_*.asp logueado y guarda HTML + tablas."""
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

# Páginas operativas que descubrimos
PAGES = [
    "clientes_per",      # Resumen Personal
    "clientes_fis",      # Físico (mercaderías)
    "clientes_des_pend", # Descargas pendientes ← ojo, acá CTGs
    "clientes_btm",      # ?
    "clientes_dol",      # USD
    "boletos",           # Boletos
    "clientes_ter",      # Terminados
    "clientes_cta",      # Cuenta
    "clientes_din",      # Dinero
]

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.allariaagro.com.ar/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    # Login si hace falta
    if "clientes.allariaagro" not in page.url:
        try:
            page.locator("a:has-text('Login')").first.click(timeout=3000)
            page.wait_for_timeout(3000)
            page.locator("input[type='text']").first.fill(USER)
            page.locator("input[type='password']").first.fill(PWD)
            page.locator("button[type='submit']").first.click()
            page.wait_for_timeout(10000)
        except: pass
    print(f"[+] URL: {page.url}")

    for pg in PAGES:
        url = f"https://clientes.allariaagro.com.ar/clientes/{pg}.asp"
        print(f"\n[+] GET {pg}.asp")
        try:
            # Probar GET primero, si no funciona, POST
            r = page.context.request.get(url, timeout=30000)
            ct = r.headers.get("content-type", "")
            txt = r.text()
            if r.status == 200 and len(txt) > 200:
                (OUT/f"page_{pg}.html").write_text(txt, encoding="utf-8", errors="ignore")
                # Buscar tablas y conteo de filas
                ntbl = len(re.findall(r'<table', txt, re.IGNORECASE))
                ntr = len(re.findall(r'<tr', txt, re.IGNORECASE))
                # Sample data
                ctg_matches = re.findall(r'\b\d{11}\b', txt)
                ctg_unique = list(set(ctg_matches))[:5]
                print(f"   [{r.status}] {len(txt)} bytes  tables={ntbl} rows={ntr}  ctgs_found={len(set(ctg_matches))}")
                if ctg_unique: print(f"   sample CTGs: {ctg_unique}")
            else:
                print(f"   [{r.status}] {len(txt)} bytes  → posible redirect/login")
        except Exception as e:
            print(f"   [!] {pg}: {str(e)[:80]}")
        time.sleep(0.5)

    ctx.close()
    print("\n[+] Done. HTMLs en", OUT)
