"""Genérico: abre Chrome con perfil persistente + puerto CDP y queda abierto,
para conectarme desde cdp_drive.py sin cerrarlo. Reutilizable por cualquier extranet.
Uso: py scripts/cdp_session.py <profile> <url> [port]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
prof = sys.argv[1] if len(sys.argv) > 1 else ".intagro_profile"
url  = sys.argv[2] if len(sys.argv) > 2 else "https://portal.intagro.com/"
port = sys.argv[3] if len(sys.argv) > 3 else "9334"
PROFILE = ROOT / "scraper" / prof
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1550,"height":950}, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled", f"--remote-debugging-port={port}"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try: page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e: print("goto:", str(e)[:60])
    print(f"[+] Chrome arriba (perfil {prof}) CDP :{port}. URL:", page.url, flush=True)
    print("[+] No cerrar la ventana.", flush=True)
    try: ctx.wait_for_event("close", timeout=0)
    except Exception: pass
    print("[+] cerrada", flush=True)
