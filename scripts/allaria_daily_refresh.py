"""Refresh diario Allaria — login + scrappear clientes_fis + clientes_cta + parse.
(2026-08: ya no hace git push; la subida es manual via /granos-tablero/subir-datos.)
Diseñado para Windows Task Scheduler. Logs en data/allaria/refresh.log."""
from __future__ import annotations
import sys, os, json, time, subprocess, re
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PROFILE = ROOT / "scripts" / "scraper" / ".allaria_profile"
DATA = ROOT / "data" / "allaria"
HTML_OUT = ROOT / "scripts" / "scraper" / "out" / "allaria"
DATA.mkdir(parents=True, exist_ok=True)
HTML_OUT.mkdir(parents=True, exist_ok=True)
LOG = DATA / "refresh.log"

USER = os.environ.get("ALLARIA_USER", "AGRONASAJA")
PWD = os.environ.get("ALLARIA_PASS", "Agronasaja.1234")

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")

def main():
    log("="*60); log("ALLARIA DAILY REFRESH START"); log("="*60)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("[!] Playwright missing"); return 1
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log("[!] BeautifulSoup4 missing"); return 1

    headless = "--visible" not in sys.argv

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width":1500,"height":950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        log("[+] Login...")
        page.goto("https://www.allariaagro.com.ar/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        try: page.locator("a:has-text('Login')").first.click(timeout=5000)
        except: pass
        page.wait_for_timeout(5000)
        try:
            page.wait_for_selector("input[type='text'], input[name='username']", timeout=15000)
            page.locator("input[type='text']").first.fill(USER, timeout=5000)
            page.locator("input[type='password']").first.fill(PWD, timeout=5000)
            page.locator("button[type='submit']").first.click(timeout=5000)
            page.wait_for_timeout(15000)
        except Exception as e:
            log(f"[!] login err: {e}"); ctx.close(); return 1
        if "clientes.allariaagro" not in page.url:
            log(f"[X] login fallo, URL={page.url}"); ctx.close(); return 1
        log(f"[+] Login OK: {page.url}")

        # Esperar CF si aparece
        try:
            body = page.inner_text("body", timeout=3000)
            if "Un momento" in body:
                log("[+] CF challenge, waiting...")
                for _ in range(20):
                    page.wait_for_timeout(2000)
                    body = page.inner_text("body", timeout=2000)
                    if "Un momento" not in body: break
        except: pass

        # Bajar mercaderias + cta. cte
        for pg in ("clientes_fis", "clientes_cta"):
            url = f"https://clientes.allariaagro.com.ar/clientes/{pg}.asp"
            log(f"[+] GET {pg}.asp")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                html = page.content()
                (HTML_OUT/f"r_{pg}.html").write_text(html, encoding="utf-8", errors="ignore")
                log(f"    {len(html)} bytes")
            except Exception as e:
                log(f"    [!] {e}")
        ctx.close()

    # Parsear
    log("[+] Parseando...")
    try:
        subprocess.run(["py", str(ROOT/"scripts"/"allaria_parse_html.py")], check=True, timeout=60)
        log("    parse OK")
    except Exception as e:
        log(f"    [!] parse err: {e}")

    # Refresh CTGs DW
    log("[+] Refresh CTGs Allaria del DW...")
    try:
        subprocess.run(["py", str(ROOT/"scripts"/"dw_find_allaria.py")], check=True, timeout=120)
        log("    DW OK")
    except Exception as e:
        log(f"    [!] DW err: {e}")

    # 2026-08: ya no se pushea a GitHub (repos dados de baja por incidente de
    # seguridad). Los JSON quedan en data/ y se suben a mano desde la extranet:
    # /granos-tablero/subir-datos -> boton "Carpeta data/..." (cuando este online).

    log("[OK] DONE"); return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as e:
        log(f"[X] FATAL: {type(e).__name__}: {e}")
        import traceback; log(traceback.format_exc())
        sys.exit(1)
