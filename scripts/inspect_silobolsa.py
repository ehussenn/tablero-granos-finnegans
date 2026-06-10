"""Verifica que la columna SILO BOLSA en Granaria se llene automatica."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import sys, re
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
INDEX = (ROOT.parent / "index.html").resolve()

with sync_playwright() as p:
    b=p.chromium.launch(headless=True); ctx=b.new_context(viewport={"width":1500,"height":1700}); pg=ctx.new_page()
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(INDEX.as_uri(), wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(3500)
    pg.locator('.nav-item[data-go-sub="pn-granaria"]').first.click(); pg.wait_for_timeout(1200)
    pg.select_option('#pn-campana', ''); pg.wait_for_timeout(1500)
    # leer PAYLOAD.stock_silobolsa
    info = pg.evaluate("(()=>{const o=window.PAYLOAD&&window.PAYLOAD.stock_silobolsa||{};return {n:Object.keys(o).length, ej:Object.entries(o).slice(0,5)};})()")
    print("PAYLOAD.stock_silobolsa:", info)
    # buscar la columna SILO BOLSA en thead y verificar valores en tbody
    thead = pg.locator("#pn-thead").inner_text()
    print("\nThead snippet:", thead.replace("\n"," | ")[:200])
    # encontrar indice de columna SILO BOLSA
    ths = [h.inner_text().strip() for h in pg.locator("#pn-thead th").all()]
    print(f"\nTotal th: {len(ths)}")
    idx_sb = None
    for i, h in enumerate(ths):
        if "SILO BOLSA" in h.upper():
            idx_sb = i; break
    print(f"Columna SILO BOLSA en posicion: {idx_sb}")
    # leer valores de esa columna para algunas filas conocidas
    rows = pg.locator("#pn-tbody tr").all()
    print(f"Total filas en cuerpo: {len(rows)}")
    print("\n=== Valores SILO BOLSA por fila (no vacios) ===")
    contado = 0
    for r in rows[:80]:
        tds = r.locator("td").all()
        if len(tds) < (idx_sb+1) if idx_sb else 5: continue
        prod_txt = tds[0].inner_text().strip()
        sb_txt = tds[idx_sb].inner_text().strip() if idx_sb else "?"
        if sb_txt and sb_txt != "—" and sb_txt != "":
            print(f"  {prod_txt:50}  →  {sb_txt}")
            contado += 1
            if contado > 25: break
    print("JS_ERRORS:", len(errs))
    for e in errs[:5]: print("  ERR:", e)
    ctx.close(); b.close()
