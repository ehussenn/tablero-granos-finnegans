"""Prueba el layout sidebar: clickea cada nav-item y verifica panel/subpanel activo + filas."""
from __future__ import annotations
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
INDEX = (ROOT.parent / "index.html").resolve()
URL = INDEX.as_uri()

CASES = [
    ("Compra · Posición General", "compra", "cp-posicion", None),
    ("Compra · Financiera",       "compra", "cp-financiera", None),
    ("Compra · Canjes",           "compra", "cp-canjes", "#tbl-body-canjes tr"),
    ("Compra · Cruce",            "compra", "cp-cruce", "[data-sub-panel='cp-cruce'] table tbody tr"),
    ("Compra · Proyectado",       "compra", "pg-pagos", "[data-sub-panel='pg-pagos'] table tbody tr"),
    ("Venta · Posición General",  "venta",  "posicion", None),
    ("Venta · Financiera",        "venta",  "financiera", None),
    ("Posición Granaria",         "posicion", "", None),
]

def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width":1500,"height":950})
        pg = ctx.new_page()
        errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)[:140]))
        pg.goto(URL, wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(3000)

        # sidebar presente?
        print("sidebar:", pg.locator(".sidebar").count(), "| nav-items:", pg.locator(".nav-item").count(), flush=True)
        print("topbar admin:", pg.locator("#btn-admin").count(), "| salir:", pg.locator(".logout-btn").count(), flush=True)

        for title, tab, sub, sel_rows in CASES:
            # clickear el nav-item correspondiente
            loc = pg.locator(f'.nav-item[data-go-tab="{tab}"][data-go-sub="{sub}"]')
            loc.first.click(); pg.wait_for_timeout(1200)
            panel_active = pg.locator(f'.panel[data-panel="{tab}"].active').count()
            topbar = pg.locator("#topbar-title").inner_text()
            extra = ""
            if sel_rows:
                extra = f" | filas={pg.locator(sel_rows).count()}"
            ok = "OK" if panel_active==1 else "FALLA"
            print(f"[{ok}] {title}: panel_active={panel_active} topbar='{topbar}'{extra}", flush=True)

        # screenshot final (en una vista con datos)
        pg.locator('.nav-item[data-go-tab="compra"][data-go-sub="cp-cruce"]').first.click(); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT/"sidebar_layout.png"), full_page=False)
        # test admin
        pg.locator("#btn-admin").click(); pg.wait_for_timeout(600)
        modal_vis = pg.locator("#pg-autobackup-modal").is_visible()
        print("admin abre modal PAT:", modal_vis, flush=True)
        pg.screenshot(path=str(OUT/"sidebar_admin.png"), full_page=False)

        print("JS_ERRORS:", len(errs), flush=True)
        for e in errs[:8]: print("  ERR:", e, flush=True)
        ctx.close(); b.close()

if __name__ == "__main__":
    main()
