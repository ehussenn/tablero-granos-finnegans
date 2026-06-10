"""Verifica el agrupador SEMILLA en Granaria."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
INDEX = (ROOT.parent / "index.html").resolve()

with sync_playwright() as p:
    b=p.chromium.launch(headless=True); ctx=b.new_context(viewport={"width":1500,"height":1700}); pg=ctx.new_page()
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(INDEX.as_uri(), wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(3500)
    pg.locator('.nav-item[data-go-sub="pn-granaria"]').first.click(); pg.wait_for_timeout(1500)
    # poner Campaña en Todas
    try: pg.select_option('#pn-campana', '')
    except: pass
    try: pg.select_option('#pn-empresa', '')
    except: pass
    pg.wait_for_timeout(1500)
    sem_headers = pg.locator("#pn-tbody .pn-semilla-header").count()
    print("Filas SEMILLA <FAM>:", sem_headers)
    for i in range(sem_headers):
        tr = pg.locator("#pn-tbody .pn-semilla-header").nth(i)
        txt = tr.locator("td.pn-prod-cell").inner_text()
        fam = tr.get_attribute("data-sem-fam")
        print(f"  -> {fam}: '{txt}'")
    grupo = pg.locator("#pn-tbody .pn-grupo").count()
    print("Filas TOTAL <FAM>:", grupo)
    total_rows = pg.locator("#pn-tbody tr").count()
    print("Total filas:", total_rows)
    # Probar expansion clickeando primera SEMILLA
    if sem_headers > 0:
        first = pg.locator("#pn-tbody .pn-semilla-header").first
        fam_first = first.get_attribute("data-sem-fam")
        first.click(); pg.wait_for_timeout(800)
        child = pg.locator("#pn-tbody .pn-semilla-child").count()
        print(f"Tras click en SEMILLA {fam_first}: filas variedades visibles = {child}")
        pg.screenshot(path=str(OUT/"granaria_expanded.png"), full_page=True)
        # colapsar
        pg.locator(f'#pn-tbody .pn-semilla-header[data-sem-fam="{fam_first}"]').click(); pg.wait_for_timeout(500)
        child2 = pg.locator("#pn-tbody .pn-semilla-child").count()
        print(f"Tras colapsar: filas variedades visibles = {child2}")
    pg.screenshot(path=str(OUT/"granaria_collapsed.png"), full_page=True)
    print("JS_ERRORS:", len(errs))
    for e in errs[:6]: print("  ERR:", e)
    ctx.close(); b.close()
