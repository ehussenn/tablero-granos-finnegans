"""Abre el index.html local en contexto fresco (sin localStorage) y verifica tabs."""
from __future__ import annotations
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out"; OUT.mkdir(parents=True, exist_ok=True)
INDEX = (ROOT.parent / "index.html").resolve()
URL = INDEX.as_uri()

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()  # contexto fresco, sin localStorage
        page = ctx.new_page()
        errors=[]
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.goto(URL, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)

        page.click('.tab[data-tab="compra"]'); page.wait_for_timeout(800)
        results={}
        for sub, sel_rows in [
            ("cp-canjes", "#tbl-body-canjes tr"),
            ("cp-cruce", "[data-sub-panel='cp-cruce'] table tbody tr"),
            ("pg-pagos", "[data-sub-panel='pg-pagos'] table tbody tr"),
        ]:
            page.click(f'.subtab[data-sub="{sub}"]'); page.wait_for_timeout(2000)
            results[sub] = page.locator(sel_rows).count()
            page.screenshot(path=str(OUT/f"local_{sub}.png"), full_page=True)

        print("FILAS canjes:", results.get("cp-canjes"), flush=True)
        print("FILAS cruce :", results.get("cp-cruce"), flush=True)
        print("FILAS pagos :", results.get("pg-pagos"), flush=True)
        print("--- JS errors ---", flush=True)
        if not errors: print("  (ninguno) OK", flush=True)
        for e in errors[:10]: print("  ERR:", e, flush=True)
        ctx.close(); browser.close()

if __name__ == "__main__":
    main()
