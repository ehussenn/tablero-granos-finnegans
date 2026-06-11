"""Allaria robust: maneja Cloudflare challenge + login + scrapping con waits largos."""
from __future__ import annotations
import sys, os, json, time, re
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".allaria_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "allaria"
DATA = ROOT / "data" / "allaria"
OUT.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("ALLARIA_USER", "AGRONASAJA")
PWD = os.environ.get("ALLARIA_PASS", "Agronasaja.1234")

PAGES = [
    ("clientes_per", "Resumen"),
    ("clientes_fis", "Físico"),
    ("clientes_des_pend", "DescargasPendientes"),
    ("clientes_btm", "Btm"),
    ("clientes_dol", "Dolares"),
    ("boletos", "Boletos"),
    ("clientes_ter", "Terminados"),
    ("clientes_cta", "CuentaCorriente"),
    ("clientes_din", "Dinero"),
]

def wait_for_cf(page, label="cf"):
    """Espera a que Cloudflare termine su challenge."""
    for i in range(25):
        page.wait_for_timeout(1500)
        try:
            body = page.inner_text("body", timeout=2000)
            if "Un momento" not in body and "moment" not in body[:200]:
                return True
        except: pass
    return False

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    print("[+] Goto landing...")
    page.goto("https://www.allariaagro.com.ar/", wait_until="domcontentloaded", timeout=60000)
    print("    Esperando 8s pa carga inicial...")
    page.wait_for_timeout(8000)
    page.screenshot(path=str(OUT/"r01_landing.png"))

    # Click Login
    print("[+] Click Login...")
    try:
        page.locator("a:has-text('Login')").first.click(timeout=5000)
        page.wait_for_timeout(5000)
    except Exception as e: print(f"  [!] {e}")
    page.screenshot(path=str(OUT/"r02_after_login_click.png"))

    # Login
    print("[+] Fill credenciales...")
    try:
        # Esperar más al campo
        page.wait_for_selector("input[type='text'], input[name='username']", timeout=15000)
        for u_sel in ["input[name='username']", "input[name='usuario']", "input[type='text']"]:
            el = page.locator(u_sel).first
            if el.count() > 0:
                el.fill(USER, timeout=5000); print(f"   user OK ({u_sel})"); break
        page.locator("input[type='password']").first.fill(PWD, timeout=5000); print(f"   pass OK")
        page.locator("button[type='submit']").first.click(timeout=5000); print(f"   submit OK")
        page.wait_for_timeout(15000)
    except Exception as e: print(f"  [!] login: {e}")
    page.screenshot(path=str(OUT/"r03_after_submit.png"))
    print(f"[+] URL post-login: {page.url}")

    # Esperar Cloudflare después del login
    if "Un momento" in page.inner_text("body", timeout=3000):
        print("[+] CF challenge detectado, esperando...")
        wait_for_cf(page)
    page.wait_for_timeout(5000)
    print(f"[+] URL final: {page.url}")

    # Visitar cada página esperando waits largos
    for pg, label in PAGES:
        url = f"https://clientes.allariaagro.com.ar/clientes/{pg}.asp"
        print(f"\n[+] {label} ({pg}.asp)...")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            # Esperar CF si aparece
            try:
                body = page.inner_text("body", timeout=2000)
                if "Un momento" in body or "moment" in body[:100]:
                    print("   CF challenge, esperando...")
                    wait_for_cf(page, label)
                    page.wait_for_timeout(3000)
            except: pass
            html = page.content()
            (OUT/f"r_{pg}.html").write_text(html, encoding="utf-8", errors="ignore")
            page.screenshot(path=str(OUT/f"r_{pg}.png"), full_page=True)
            # Analisis
            ntbl = len(re.findall(r'<table', html, re.IGNORECASE))
            ctg_matches = re.findall(r'\b\d{11}\b', html)
            ctg_unique = sorted(set(ctg_matches))
            print(f"   {len(html)} bytes  tables={ntbl}  CTGs unicos={len(ctg_unique)}")
            if ctg_unique[:5]: print(f"   sample CTGs: {ctg_unique[:5]}")
        except Exception as e:
            print(f"   [!] err: {str(e)[:120]}")
        time.sleep(2)

    page.wait_for_timeout(10000)
    print("\n[+] Done.")
    ctx.close()
