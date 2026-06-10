"""Brute-force endpoint detail con el key conocido. Usar Bearer ya capturado."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
OUT.mkdir(parents=True, exist_ok=True)

CUSTOMER_ID = "35188546"
API = "https://api.cglcloud.com/api/dxo/gps"
KEY = "51240-E1-02056985-000-1000"
MOV_NUM = "51240-E1-02056985"
TOKEN = [None]

def on_req(r):
    if "api.cglcloud.com" in r.url and r.method == "GET":
        a = r.headers.get("authorization")
        if a and not TOKEN[0]: TOKEN[0] = a

CALLS_DURING_CLICK = []
def on_resp(r):
    if "api.cglcloud.com" in r.url:
        try:
            CALLS_DURING_CLICK.append({"url": r.url, "status": r.status, "method": r.request.method})
        except: pass

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(10000)
    if not TOKEN[0]: print("[!] no token"); ctx.close(); sys.exit(1)
    print(f"[+] Token capturado\n")

    # Ahora capturar las llamadas al hacer click
    page.on("response", on_resp)

    # Buscar el primer row clickeable (intentar varios selectores)
    print("[+] Intentando clickear primera fila...")
    clicked = False
    for sel in [
        '[class*="MuiTableRow"]:not([class*="head"])',
        'tbody tr',
        '[role="row"]:not(:first-child)',
        '.row:not(.header)',
        'div[class*="Row"]:has(span[class*="value"])',
    ]:
        try:
            els = page.locator(sel).all()
            if els and len(els) > 1:
                # Click en el 2do (saltear header si lo hay)
                target = els[1] if len(els) > 1 else els[0]
                target.click(timeout=4000)
                clicked = True
                print(f"    ✓ click con: {sel}")
                page.wait_for_timeout(8000)
                break
        except Exception as e:
            pass
    if not clicked: print("    [!] no pude clickear ninguna fila")

    page.screenshot(path=str(OUT/"cargill_after_click.png"), full_page=True)
    print(f"\n[+] URL despues del click: {page.url}")
    print(f"[+] Calls API durante click: {len(CALLS_DURING_CLICK)}")
    for c in CALLS_DURING_CLICK:
        print(f"   {c['method']} [{c['status']}] {c['url'][:160]}")

    # Tambien probar endpoints brute-force directos con el key conocido
    print(f"\n[+] Brute-force endpoints con key '{KEY}'...")
    H = {"authorization": TOKEN[0], "accept":"application/json"}

    common = f"customerId={CUSTOMER_ID}&source=JDEAR&role=DXP_GPS_Role_Client"
    candidates = [
        f"/v1/movements/{KEY}?{common}",
        f"/v1/movements/{KEY}/details?{common}",
        f"/v1/movement/{KEY}?{common}",
        f"/v1/movements/details/{KEY}?{common}",
        f"/v1/movements/{MOV_NUM}?{common}",
        f"/v1/movements/{MOV_NUM}/details?{common}",
        f"/v1/movement/{MOV_NUM}?{common}",
        f"/v1/quality?{common}&key={KEY}",
        f"/v1/quality?{common}&movementNumber={MOV_NUM}",
        f"/v1/movements/quality?{common}&movementNumber={MOV_NUM}",
        f"/v1/services?{common}&movementNumber={MOV_NUM}",
        f"/v1/movements/services?{common}&movementNumber={MOV_NUM}",
        f"/v2/movements/{KEY}?{common}",
        f"/v2/movements/{MOV_NUM}/details?{common}",
        f"/v1/movements/qualityAnalysis?{common}&key={KEY}",
        f"/v2/movement/qualityAnalysis?{common}&movementNumber={MOV_NUM}",
    ]
    for ep in candidates:
        url = f"{API}{ep}"
        try:
            r = page.context.request.get(url, headers=H, timeout=15000)
            if r.status == 200:
                j = r.json()
                size = len(json.dumps(j))
                print(f"  ✓ [{r.status}] {ep[:100]}  size={size}")
                if size > 200:
                    keys_str = ""
                    if isinstance(j, dict): keys_str = f" KEYS: {list(j.keys())[:10]}"
                    if isinstance(j, dict) and "data" in j and isinstance(j["data"], dict):
                        keys_str += f" data.KEYS: {list(j['data'].keys())[:15]}"
                    print(f"      {keys_str}")
                    (OUT/f"detail_{ep.split('?')[0].replace('/','_')}.json").write_text(json.dumps(j, indent=2, ensure_ascii=False)[:5000], encoding="utf-8")
            elif r.status not in (401, 403, 404):
                print(f"  · [{r.status}] {ep[:100]}: {r.text()[:80]}")
        except Exception as e:
            pass

    ctx.close()
print("\n[+] Done.")
