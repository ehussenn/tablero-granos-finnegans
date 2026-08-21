"""Refresh diario LDC — corre headless y baja settlements + fixations a data/ldc/.
(2026-08: ya no hace git push; la subida es manual via /granos-tablero/subir-datos.)
Diseñado para Windows Task Scheduler. Logs a data/ldc/refresh.log.
Replica del patrón de cargill_daily_refresh.py."""
from __future__ import annotations
import sys, os, json, time, subprocess
from pathlib import Path
from datetime import datetime
from _env import need

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
DATA = ROOT / "data" / "ldc"
DATA.mkdir(parents=True, exist_ok=True)
LOG = DATA / "refresh.log"

USER = need("LDC_USER")
PWD = need("LDC_PASS")
CUIT = "30710712758"
API = "https://mildc.com/Dreyfus.Extranet.Site.UI.Services/api"

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def main():
    log("="*60); log("LDC DAILY REFRESH START"); log("="*60)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("[!] Playwright no instalado, abortando"); return 1

    TOKEN = [None]
    def on_req(r):
        if "mildc.com" in r.url:
            a = r.headers.get("authorization", "")
            if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a

    headless = "--visible" not in sys.argv

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width":1500,"height":950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("request", on_req)

        log("[+] Abriendo portal LDC...")
        page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        if page.locator("input[type='password']").count() > 0:
            log("[+] Re-login (sesion expirada)...")
            try:
                page.locator("input[type='text']").first.fill(USER)
                page.locator("input[type='password']").first.fill(PWD)
                page.locator("button[type='submit']").first.click()
                page.wait_for_timeout(12000)
            except Exception as e:
                log(f"[!] login err: {e}"); ctx.close(); return 1
        page.goto("https://mildc.com/webportal/dashboard", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        if not TOKEN[0]:
            log("[X] No se capturo Bearer, abortando"); ctx.close(); return 1
        log(f"[+] Token OK")

        H = {"authorization": TOKEN[0], "accept": "application/json", "content-type": "application/json"}
        DATE_FROM_ISO = "2026-01-01T00:00:00.000Z"
        DATE_UNTIL_ISO = datetime.now().strftime("%Y-%m-%dT23:59:59.999Z")
        DATE_FROM_DDMM = "01/01/2026"
        DATE_UNTIL_DDMM = datetime.now().strftime("%d/%m/%Y")

        def fetch(endpoint, body, label):
            try:
                r = page.context.request.post(f"{API}{endpoint}", headers=H,
                       data=json.dumps(body), timeout=60000)
                if r.status == 200:
                    j = r.json()
                    if isinstance(j, dict) and "List" in j and isinstance(j["List"], list):
                        log(f"   {label}: {len(j['List'])} items"); return j["List"]
                    if isinstance(j, list):
                        log(f"   {label}: {len(j)} items"); return j
                    log(f"   {label}: estructura desconocida"); return j
                else:
                    log(f"   {label}: ERR {r.status}"); return None
            except Exception as e:
                log(f"   {label}: exc {str(e)[:80]}"); return None

        log("[+] Bajando Liquidaciones (Settlements)...")
        s = fetch("/Settlements/ReadByCriteriaSettled", {
            "ConditionalKey": "GroupD", "IssueDateFrom": DATE_FROM_DDMM,
            "IssueDateUntil": DATE_UNTIL_DDMM, "CompanyDocumentNumber": CUIT,
        }, "settlements")

        log("[+] Bajando Fijaciones (Fixations)...")
        f = fetch("/Fixations/ReadByCriteria", {
            "ConditionalKey": "GroupB", "DateFrom": DATE_FROM_ISO,
            "DateUntil": DATE_UNTIL_ISO, "CompanyDocumentNumber": CUIT, "isMassiveSearch": True,
        }, "fixations")

        log("[+] Bajando NotAppliedProduct...")
        n = fetch("/NotAppliedProduct/ListTotalsByProduct", {
            "ConditionalKey": "GroupB", "DateFrom": DATE_FROM_ISO,
            "DateUntil": DATE_UNTIL_ISO, "CounterpartID": None,
        }, "not_applied")

        ctx.close()

    if s is None and f is None:
        log("[!] Settlements y Fixations vacios — no commiteo"); return 1

    # Guardar (estructura uniforme: lista directa, no envuelta)
    if s is not None: (DATA/"settlements.json").write_text(json.dumps({"List":s}, ensure_ascii=False, indent=2), encoding="utf-8")
    if f is not None: (DATA/"fixations.json").write_text(json.dumps({"List":f}, ensure_ascii=False, indent=2), encoding="utf-8")
    if n is not None: (DATA/"not_applied_product.json").write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
    log("[+] JSONs guardados")

    # Refresh CTGs LDC del DW
    log("[+] Refrescando CTGs LDC del DW...")
    try:
        subprocess.run(["py", str(ROOT/"scripts"/"dw_extract_ldc_ctgs.py")], check=True, timeout=120)
        log("    -> CTGs DW actualizados")
    except Exception as e:
        log(f"    [!] err DW: {e}")

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
