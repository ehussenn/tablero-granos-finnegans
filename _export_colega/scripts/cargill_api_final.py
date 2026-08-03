"""Cargill scraper final: baja movements + payments + invoices completos.
Estructura confirmada: { data: { metadata, movements/payments/invoices: [...] }, statusCode }"""
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
DATA = ROOT.parent / "data" / "cargill"
DATA.mkdir(parents=True, exist_ok=True)

CUSTOMER_ID = "35188546"
API = "https://api.cglcloud.com/api/dxo/gps"
TOKEN = None

def on_req(r):
    global TOKEN
    if "api.cglcloud.com" in r.url and r.method == "GET":
        a = r.headers.get("authorization")
        if a and not TOKEN: TOKEN = a

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    if not TOKEN: print("[!] no token"); ctx.close(); sys.exit(1)
    print(f"[+] Token capturado", flush=True)

    H = {"authorization": TOKEN, "accept":"application/json"}

    def fetch_all(endpoint, items_key, extra_params=None, page_size=200):
        """Pagina via offset/limit. items_key = 'movements' | 'payments' | 'invoices'."""
        all_rows = []
        offset = 0
        meta_total = None
        while True:
            params = {"customerId":CUSTOMER_ID,"source":"JDEAR","role":"DXP_GPS_Role_Client",
                      "offset":offset, "limit":page_size}
            if extra_params: params.update(extra_params)
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{API}{endpoint}?{qs}"
            try:
                r = page.context.request.get(url, headers=H, timeout=30000)
                if r.status != 200:
                    print(f"    [!] {r.status} offset={offset}")
                    break
                j = r.json()
                data = j.get("data") or {}
                items = data.get(items_key) or []
                if not items: break
                all_rows.extend(items)
                meta = data.get("metadata") or {}
                meta_total = meta.get("total") or meta.get("totalElements") or meta_total
                print(f"    {endpoint}: offset={offset} got={len(items)} acum={len(all_rows)}" + (f" / tot={meta_total}" if meta_total else ""), flush=True)
                offset += page_size
                if meta_total and len(all_rows) >= meta_total: break
                if len(items) < page_size: break
                time.sleep(0.3)
            except Exception as e:
                print(f"    [!] err: {e}"); break
        return all_rows

    print(f"\n[+] MOVEMENTS")
    movs = fetch_all("/v1/movements", "movements", {"sortBy":"loadUnloadDate","sort":"desc","legalDocument":""})
    (DATA/"movements.json").write_text(json.dumps(movs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {len(movs)} guardados")
    if movs and isinstance(movs[0], dict):
        print(f"  COLS: {list(movs[0].keys())}")

    print(f"\n[+] PAYMENTS")
    pays = fetch_all("/v1/payments", "payments")
    (DATA/"payments.json").write_text(json.dumps(pays, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {len(pays)} guardados")
    if pays and isinstance(pays[0], dict):
        print(f"  COLS: {list(pays[0].keys())}")

    print(f"\n[+] INVOICES")
    invs = fetch_all("/v1/invoices", "invoices")
    (DATA/"invoices.json").write_text(json.dumps(invs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {len(invs)} guardados")
    if invs and isinstance(invs[0], dict):
        print(f"  COLS: {list(invs[0].keys())}")

    # Mostrar una fila completa de movements (con todas las columnas no vacias)
    if movs and isinstance(movs[0], dict):
        print(f"\n[+] Sample MOVEMENT fila 1 (campos no vacios):")
        for k, v in movs[0].items():
            if v not in (None, "", 0, "0", "0.0", []):
                print(f"    {k:<35} = {repr(v)[:80]}")
    if invs and isinstance(invs[0], dict):
        print(f"\n[+] Sample INVOICE fila 1 (campos no vacios):")
        for k, v in invs[0].items():
            if v not in (None, "", 0, "0", "0.0", []):
                print(f"    {k:<35} = {repr(v)[:80]}")

    ctx.close()
print("\n[+] Done.")
