"""Levanta Chrome con el perfil bunge + puerto CDP (9333) y queda abierto.
Yo me conecto desde bunge_drive.py SIN cerrarlo (sesión/ CAPTCHA una sola vez)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".bunge_profile"
URL = "https://operacionesbasa.bunge.ar/operacionesbasa/Login.aspx"
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1550,"height":950}, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled","--remote-debugging-port=9333"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try: page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e: print("goto:", str(e)[:60])
    print("[+] Chrome bunge arriba con CDP en :9333. URL:", page.url, flush=True)
    print("[+] Si pide login, logueate (CAPTCHA). No cerrar la ventana.", flush=True)
    try: ctx.wait_for_event("close", timeout=0)
    except Exception: pass
    print("[+] cerrada", flush=True)
