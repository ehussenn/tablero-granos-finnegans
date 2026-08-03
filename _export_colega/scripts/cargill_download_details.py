"""Baja el detalle (análisis calidad + servicios) de TODOS los movements de Cargill.
Toma el movementNumber de cada uno en movements.json, llama /v1/movements/{num}, guarda el detalle."""
import sys, json, time, os
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
DATA = ROOT.parent / "data" / "cargill"
DATA.mkdir(parents=True, exist_ok=True)

CUSTOMER_ID = "35188546"
API = "https://api.cglcloud.com/api/dxo/gps"
TOKEN = [None]

def on_req(r):
    if "api.cglcloud.com" in r.url and r.method == "GET":
        a = r.headers.get("authorization")
        if a and not TOKEN[0]: TOKEN[0] = a

movs_file = DATA / "movements.json"
if not movs_file.exists():
    print("[!] data/cargill/movements.json no existe — correr cargill_api_final.py antes")
    sys.exit(1)
movs = json.loads(movs_file.read_text(encoding="utf-8"))
print(f"[+] {len(movs)} movements a procesar")

# Si ya tenemos detalles previos, saltearlos para hacerlo incremental
details_file = DATA / "movements_detail.json"
existing = {}
if details_file.exists():
    try:
        existing = json.loads(details_file.read_text(encoding="utf-8"))
        print(f"[+] {len(existing)} ya descargados — incremental mode")
    except: existing = {}

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(15000)
    if "/login" in page.url:
        u = os.environ.get("CARGILL_USER", "santiagolm@agronasaja.com.ar")
        w = os.environ.get("CARGILL_PASS", "<PASSWORD>")
        page.locator("input[name='username']").fill(u)
        page.locator("input[name='password']").fill(w)
        page.locator("input[type='submit']").click()
        page.wait_for_timeout(8000)
        page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(15000)
    if not TOKEN[0]: print("[!] no token"); ctx.close(); sys.exit(1)
    print(f"[+] Token OK\n")

    H = {"authorization": TOKEN[0], "accept":"application/json"}
    common = f"customerId={CUSTOMER_ID}&source=JDEAR&role=DXP_GPS_Role_Client"

    results = dict(existing)
    todo = [m for m in movs if m.get("movementNumber") and m["movementNumber"] not in results]
    print(f"[+] {len(todo)} a bajar nuevos")
    start = time.time()

    for i, m in enumerate(todo, 1):
        num = m["movementNumber"]
        url = f"{API}/v1/movements/{num}?{common}"
        try:
            r = page.context.request.get(url, headers=H, timeout=15000)
            if r.status == 200:
                j = r.json()
                detail = (j.get("data") or {}).get("movementsDetail")
                if detail: results[num] = detail
            elif r.status == 500:
                pass  # detalle no disponible
            else:
                print(f"  [{i}/{len(todo)}] {num} status={r.status}")
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {num} err: {str(e)[:60]}")
        # Progress + save incremental cada 100
        if i % 100 == 0:
            elapsed = time.time() - start
            rate = i / elapsed
            eta = (len(todo) - i) / rate
            print(f"  [{i}/{len(todo)}] OK={len(results)} | {rate:.1f}/s | ETA {eta/60:.1f}min", flush=True)
            details_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(0.1)

    details_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] {len(results)} detalles guardados en data/cargill/movements_detail.json")
    print(f"[+] Total time: {(time.time()-start)/60:.1f}min")
    ctx.close()
print("[+] Done.")
