"""Navega a /movements (descargas) y /invoicesandliquidations (gastos) en Cargill GPS.
Saca screenshots + dump del HTML de cada tabla para entender la estructura."""
import sys, os, json
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
OUT.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("movements", "https://www.mycargill.com/cascsa/es/pages/movements"),
    ("invoicesandliquidations", "https://www.mycargill.com/cascsa/es/pages/invoicesandliquidations"),
    ("contracts", "https://www.mycargill.com/cascsa/es/pages/contracts"),
]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    for name, url in PAGES:
        print(f"\n{'='*80}\n[+] Navegando a {name}: {url}", flush=True)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"    [!] goto error: {e}")
            continue
        page.wait_for_timeout(6000)  # esperar render
        page.screenshot(path=str(OUT/f"cargill_{name}.png"), full_page=True)
        print(f"    Screenshot: cargill_{name}.png")
        print(f"    URL: {page.url}")
        print(f"    Title: {page.title()}")

        # Listar headers de tablas que aparezcan
        try:
            tables = page.locator("table").all()
            print(f"    Tablas encontradas: {len(tables)}")
            for i, t in enumerate(tables[:5]):
                try:
                    headers = t.locator("thead th, thead td").all_inner_texts()
                    rows = t.locator("tbody tr").count()
                    print(f"      Tabla #{i+1}: {rows} filas")
                    print(f"        Headers: {headers[:20]}")
                except Exception as e: print(f"        err: {e}")
        except Exception as e: print(f"    err tablas: {e}")

        # Buscar también divs/grids estructurados (Material UI table, etc)
        try:
            for grid_sel in ['[role="table"]', '[role="grid"]', '.MuiTable-root', '.mat-table', '.ag-root']:
                els = page.locator(grid_sel)
                if els.count():
                    print(f"    Grid {grid_sel}: {els.count()} elementos")
                    try:
                        headers = els.first.locator('[role="columnheader"], .mat-header-cell, .MuiTableCell-head, .ag-header-cell').all_inner_texts()
                        if headers: print(f"      Headers: {headers[:20]}")
                    except: pass
        except: pass

        # Dump del HTML de la primera tabla/grid para análisis
        try:
            tbl = page.locator("table, [role='grid'], [role='table'], .MuiTable-root").first
            if tbl.count():
                html = tbl.inner_html(timeout=5000)
                (OUT/f"cargill_{name}_table.html").write_text(html, encoding="utf-8")
                print(f"    HTML guardado: cargill_{name}_table.html ({len(html)} chars)")
        except Exception as e: print(f"    err html dump: {e}")

    print(f"\n[+] Done. Dejá la ventana abierta para revisar.")
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
