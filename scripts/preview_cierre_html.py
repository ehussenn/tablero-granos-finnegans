"""Captura el Estado_Patrimonial_29-05-2026.html"""
from pathlib import Path
from playwright.sync_api import sync_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')

FILE = Path(r"C:\Users\Public\Documents\Granos\Estado_Patrimonial_29-05-2026.html")
OUT = Path(__file__).resolve().parent / "scraper" / "out"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1200, "height":2000})
    pg.goto(FILE.as_uri(), wait_until="domcontentloaded")
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(OUT/"cierre_patrimonial.png"), full_page=True)
    print("OK")
    b.close()
