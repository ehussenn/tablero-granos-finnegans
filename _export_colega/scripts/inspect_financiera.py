"""Verifica la nueva subpestana Financiera local."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
INDEX = (ROOT.parent / "index.html").resolve()

with sync_playwright() as p:
    b=p.chromium.launch(headless=True); ctx=b.new_context(viewport={"width":1500,"height":1600}); pg=ctx.new_page()
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(INDEX.as_uri(), wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(3500)
    # ir a Financiera via sidebar
    pg.locator('.nav-item[data-go-sub="pn-financiera"]').first.click(); pg.wait_for_timeout(2000)
    sp = pg.locator("[data-sub-panel='pn-financiera']")
    print("subpanel financiera visible:", sp.is_visible(), flush=True)
    print("KPI cards:", pg.locator("#fn-kpis .kpi").count(), flush=True)
    print("Venta filas:", pg.locator("#fn-tbl-vta tbody tr").count(), flush=True)
    print("Compra filas:", pg.locator("#fn-tbl-cpr tbody tr").count(), flush=True)
    print("Stock filas:", pg.locator("#fn-tbl-stk tbody tr").count(), flush=True)
    print("Pendientes filas:", pg.locator("#fn-tbl-pdt tbody tr").count(), flush=True)
    # leer texto del footer venta para ver totales
    txt = pg.locator("#fn-tbl-vta tfoot").inner_text()
    print("Venta TOTAL footer:", txt.replace("\n"," | ")[:200], flush=True)
    pg.screenshot(path=str(OUT/"financiera.png"), full_page=True)
    print("JS_ERRORS:", len(errs), flush=True)
    for e in errs[:6]: print("  ERR:", e, flush=True)
    ctx.close(); b.close()
