"""Inspecciona la respuesta COMPLETA del endpoint detalle para encontrar todos los campos (servicios, fletes, etc)."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
from _env import need

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"

CUSTOMER_ID = "35188546"
TOKEN = [None]
def on_req(r):
    if "api.cglcloud.com" in r.url and r.method == "GET":
        a = r.headers.get("authorization")
        if a and not TOKEN[0]: TOKEN[0] = a

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    print("[+] esperando 15s para que cargue...")
    page.wait_for_timeout(15000)
    print(f"[+] URL: {page.url}")
    if "/login" in page.url:
        # Re-loguear
        u = need("CARGILL_USER"); w = "<PASSWORD>"
        try:
            page.locator("input[name='username']").fill(u)
            page.locator("input[name='password']").fill(w)
            page.locator("input[type='submit']").click()
            page.wait_for_timeout(8000)
            page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(15000)
            print(f"[+] post-relogin URL: {page.url}")
        except Exception as e:
            print(f"[!] relogin fallo: {e}")
    if not TOKEN[0]: print("[!] no token"); ctx.close(); sys.exit(1)
    print(f"[+] Token OK\n")

    H = {"authorization": TOKEN[0], "accept":"application/json"}
    common = f"customerId={CUSTOMER_ID}&source=JDEAR&role=DXP_GPS_Role_Client"
    url = f"https://api.cglcloud.com/api/dxo/gps/v1/movements/51240-E1-02056985?{common}"

    r = page.context.request.get(url, headers=H, timeout=20000)
    j = r.json()
    # Guardar completo
    (OUT/"cargill_detail_full.json").write_text(json.dumps(j, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Detail size: {len(json.dumps(j))} bytes")
    detail = j.get("data", {}).get("movementsDetail", {})
    print(f"[+] movementsDetail KEYS ({len(detail.keys())}):")
    for k in sorted(detail.keys()):
        v = detail[k]
        vstr = json.dumps(v) if not isinstance(v, (str, int, float, bool)) else repr(v)
        print(f"   {k:<35} = {vstr[:100]}")
    ctx.close()
