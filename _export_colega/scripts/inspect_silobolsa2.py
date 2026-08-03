"""Inspecciona col SILO BOLSA con indice correcto."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
INDEX = (ROOT.parent / "index.html").resolve()

with sync_playwright() as p:
    b=p.chromium.launch(headless=True); ctx=b.new_context(viewport={"width":1500,"height":1700}); pg=ctx.new_page()
    pg.goto(INDEX.as_uri(), wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(3500)
    pg.locator('.nav-item[data-go-sub="pn-granaria"]').first.click(); pg.wait_for_timeout(1200)
    pg.select_option('#pn-campana', ''); pg.wait_for_timeout(1500)
    # td index para silo bolsa = 4 (PRODUCTO, TOTAL_planta, SILO, BOLSAS, SILO_BOLSA)
    IDX_SB = 4
    rows = pg.locator("#pn-tbody tr").all()
    print(f"Total filas body: {len(rows)}")
    # Expandir SEMILLA SOJA para ver variedades
    sem_soja = pg.locator('#pn-tbody .pn-semilla-header[data-sem-fam="SOJA"]').first
    if sem_soja.count(): sem_soja.click(); pg.wait_for_timeout(1000)
    sem_trigo = pg.locator('#pn-tbody .pn-semilla-header[data-sem-fam="TRIGO"]').first
    if sem_trigo.count(): sem_trigo.click(); pg.wait_for_timeout(1000)
    rows = pg.locator("#pn-tbody tr").all()
    print(f"Filas tras expandir SOJA+TRIGO: {len(rows)}")
    print("\n=== Columna SILO BOLSA por fila ===")
    found = 0
    for r in rows:
        tds = r.locator("td").all()
        if len(tds) < IDX_SB+1: continue
        prod = tds[0].inner_text().strip()
        sb = tds[IDX_SB].inner_text().strip()
        if sb and sb != '—':
            print(f"  {prod[:55]:55}  SILO BOLSA = {sb}")
            found += 1
    print(f"\nTotal con valor: {found}")
    ctx.close(); b.close()
