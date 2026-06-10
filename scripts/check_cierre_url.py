"""Verifica si la URL del cierre ya está publicada."""
from playwright.sync_api import sync_playwright
import sys
sys.stdout.reconfigure(encoding='utf-8')

URL = "https://ehussenn.github.io/tablero-granos-finnegans/cierres/29-05-2026.html"
URLPARENT = "https://ehussenn.github.io/tablero-granos-finnegans/"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    try:
        resp = pg.goto(URL, wait_until="domcontentloaded", timeout=30000)
        print(f"URL cierre: {resp.status if resp else '?'}  url final: {pg.url}")
    except Exception as e:
        print(f"URL cierre ERR: {str(e)[:120]}")
    try:
        resp = pg.goto(URLPARENT + "cierres/", wait_until="domcontentloaded", timeout=20000)
        print(f"Dir cierres/: {resp.status if resp else '?'}")
    except Exception as e:
        print(f"Dir cierres ERR: {str(e)[:120]}")
    try:
        resp = pg.goto(URLPARENT, wait_until="domcontentloaded", timeout=20000)
        print(f"Pages root: {resp.status if resp else '?'}  url final: {pg.url}")
    except Exception as e:
        print(f"Pages root ERR: {str(e)[:120]}")
    b.close()
