"""Refresh diario Cargill GPS — corre headless, baja JSON, hace git push.
Diseñado para Windows Task Scheduler. Logs a data/cargill/refresh.log."""
from __future__ import annotations
import sys, os, json, time, subprocess
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

# Cargar .env del repo si existe
ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PROFILE = ROOT / "scripts" / "scraper" / ".cargill_profile"
DATA = ROOT / "data" / "cargill"
DATA.mkdir(parents=True, exist_ok=True)
LOG = DATA / "refresh.log"

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

CUSTOMER_ID = "35188546"
API = "https://api.cglcloud.com/api/dxo/gps"

def relogin_if_needed(page, headless):
    """Si la sesión expiró, re-login con CARGILL_USER/CARGILL_PASS."""
    if "/login" not in page.url and "/auth" not in page.url:
        return True
    user = os.environ.get("CARGILL_USER")
    pwd = os.environ.get("CARGILL_PASS")
    if not (user and pwd):
        log("[!] Sesion expirada y no hay CARGILL_USER/CARGILL_PASS en .env"); return False
    log("[+] Sesion expirada, re-logueando...")
    try:
        page.locator("input[name='username']").first.fill(user)
        page.locator("input[name='password']").first.fill(pwd)
        page.locator("input[type='submit']").first.click()
        page.wait_for_timeout(8000)
        return "/login" not in page.url
    except Exception as e:
        log(f"[!] Error login: {e}"); return False

def fetch_all_via_browser(page, headers, endpoint, items_key, extra_params=None, page_size=200):
    all_rows = []
    offset = 0
    while True:
        params = {"customerId":CUSTOMER_ID,"source":"JDEAR","role":"DXP_GPS_Role_Client",
                  "offset":offset, "limit":page_size}
        if extra_params: params.update(extra_params)
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{API}{endpoint}?{qs}"
        try:
            r = page.context.request.get(url, headers=headers, timeout=30000)
            if r.status != 200:
                log(f"    [!] {endpoint} {r.status} offset={offset}"); break
            j = r.json()
            data = (j.get("data") or {})
            items = data.get(items_key) or []
            if not items: break
            all_rows.extend(items)
            meta = data.get("metadata") or {}
            tot = meta.get("total") or meta.get("totalElements")
            offset += page_size
            if tot and len(all_rows) >= tot: break
            if len(items) < page_size: break
            time.sleep(0.3)
        except Exception as e:
            log(f"    [!] err: {e}"); break
    return all_rows

def main():
    log("="*60)
    log("CARGILL DAILY REFRESH START")
    log("="*60)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("[!] Playwright no instalado, abortando")
        return 1

    TOKEN = [None]
    def on_req(r):
        if "api.cglcloud.com" in r.url and r.method == "GET":
            a = r.headers.get("authorization")
            if a and not TOKEN[0]: TOKEN[0] = a

    headless = "--visible" not in sys.argv  # default headless
    log(f"[+] headless={headless}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width":1500,"height":950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("request", on_req)

        log("[+] Abriendo /Movements para capturar token...")
        page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)

        # Si redirigió a login, intentar re-login
        if "/login" in page.url:
            if not relogin_if_needed(page, headless):
                log("[X] No pude re-loguear, abortando"); ctx.close(); return 1
            page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(10000)

        if not TOKEN[0]:
            log("[X] No se capturo Bearer token, abortando"); ctx.close(); return 1
        log(f"[+] Token OK")

        H = {"authorization": TOKEN[0], "accept": "application/json"}

        log("[+] Bajando MOVEMENTS...")
        movs = fetch_all_via_browser(page, H, "/v1/movements", "movements",
                                       {"sortBy":"loadUnloadDate","sort":"desc","legalDocument":""})
        log(f"    -> {len(movs)} movements")

        log("[+] Bajando PAYMENTS...")
        pays = fetch_all_via_browser(page, H, "/v1/payments", "payments")
        log(f"    -> {len(pays)} payments")

        log("[+] Bajando INVOICES...")
        invs = fetch_all_via_browser(page, H, "/v1/invoices", "invoices")
        log(f"    -> {len(invs)} invoices")

        ctx.close()

    if not (movs and pays and invs):
        log("[!] Algun dataset esta vacio — no commiteo (probablemente sesion fallo)")
        return 1

    (DATA/"movements.json").write_text(json.dumps(movs, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA/"payments.json").write_text(json.dumps(pays, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA/"invoices.json").write_text(json.dumps(invs, ensure_ascii=False, indent=2), encoding="utf-8")
    log("[+] JSONs guardados en data/cargill/")

    # Git commit + push
    try:
        os.chdir(str(ROOT))
        subprocess.run(["git", "add", "data/cargill/movements.json",
                         "data/cargill/payments.json", "data/cargill/invoices.json"], check=True)
        # Solo commitear si hay cambios
        r = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True)
        if r.stdout.strip():
            msg = f"Cargill auto-refresh {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            subprocess.run(["git", "commit", "-m", msg], check=True)
            subprocess.run(["git", "push"], check=True)
            log(f"[+] git push OK: {msg}")
        else:
            log("[.] Sin cambios, no commit")
    except subprocess.CalledProcessError as e:
        log(f"[!] git error: {e}")
        return 1

    log("[OK] DONE")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"[X] FATAL: {type(e).__name__}: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
