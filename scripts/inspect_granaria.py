"""Captura Posicion Granaria localmente y reporta dimensiones de la tabla."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
INDEX = (ROOT.parent / "index.html").resolve()

with sync_playwright() as p:
    b=p.chromium.launch(headless=True); ctx=b.new_context(viewport={"width":1500,"height":1400}); pg=ctx.new_page()
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(INDEX.as_uri(), wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(3500)
    pg.locator('.nav-item[data-go-tab="posicion"]').first.click(); pg.wait_for_timeout(2000)
    panel = pg.locator(".panel[data-panel='posicion']")
    print("panel visible:", panel.is_visible(), flush=True)
    # KPIs cards
    cards = pg.locator("#pn-cards > *").count()
    print("KPI cards por cultivo:", cards, flush=True)
    # Tabla
    headers = pg.locator("#pn-thead th").count()
    rows = pg.locator("#pn-tbody tr").count()
    cells_first_row = pg.locator("#pn-tbody tr").first.locator("td").count() if rows else 0
    print(f"Tabla: head th={headers}  rows={rows}  cells_first_row={cells_first_row}", flush=True)
    # Texto del header (saber columnas)
    if headers:
        ths = [th.inner_text() for th in pg.locator("#pn-thead th").all()[:30]]
        print("Columnas:", ths, flush=True)
    # Texto primeras 3 filas
    for i in range(min(3, rows)):
        tds = pg.locator("#pn-tbody tr").nth(i).locator("td").all()
        vals = [t.inner_text().replace("\n","|")[:18] for t in tds[:30]]
        print(f"  row{i}:", vals, flush=True)
    # Filtros
    print("Campanas:", [o.inner_text() for o in pg.locator("#pn-campana option").all()][:10], flush=True)
    print("Empresas:", [o.inner_text() for o in pg.locator("#pn-empresa option").all()][:10], flush=True)
    pg.screenshot(path=str(OUT/"granaria.png"), full_page=True)
    print("JS_ERRORS:", len(errs), flush=True)
    for e in errs[:5]: print("  ERR:", e, flush=True)
    ctx.close(); b.close()
