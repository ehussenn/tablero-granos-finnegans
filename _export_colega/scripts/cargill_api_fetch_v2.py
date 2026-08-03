"""Captura el Authorization header de una request exitosa de Cargill, después lo replay."""
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
DATA = ROOT.parent / "data" / "cargill"
DATA.mkdir(parents=True, exist_ok=True)

CUSTOMER_ID = "35188546"
API_BASE = "https://api.cglcloud.com/api/dxo/gps"

CAPTURED_TOKEN = None
CAPTURED_HEADERS = {}

def on_request(req):
    global CAPTURED_TOKEN, CAPTURED_HEADERS
    if "api.cglcloud.com" in req.url and req.method == "GET":
        h = req.headers
        auth = h.get("authorization") or h.get("Authorization")
        if auth and not CAPTURED_TOKEN:
            CAPTURED_TOKEN = auth
            CAPTURED_HEADERS = dict(h)
            print(f"[✓] Token capturado: {auth[:60]}...", flush=True)
            print(f"    Headers extra: {list(h.keys())[:15]}", flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_request)

    print("[+] Cargando /Movements para capturar Authorization header...", flush=True)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(10000)

    if not CAPTURED_TOKEN:
        print("[!] No se capturó token. Imprimiendo localStorage para debug...", flush=True)
        ls = page.evaluate("() => ({ ls: Object.fromEntries(Object.entries(localStorage)), ss: Object.fromEntries(Object.entries(sessionStorage)) })")
        for k, v in ls["ls"].items():
            print(f"  LS[{k[:40]}] = {repr(v)[:80]}")
        for k, v in ls["ss"].items():
            print(f"  SS[{k[:40]}] = {repr(v)[:80]}")
        ctx.close(); sys.exit(1)

    # Headers a replicar (sin algunas que requests/urllib agregan solas)
    headers_to_use = {k: v for k, v in CAPTURED_HEADERS.items()
                      if k.lower() not in ("host","content-length","accept-encoding","connection")}
    print(f"\n[+] Headers que vamos a usar: {list(headers_to_use.keys())}", flush=True)

    def fetch_paged(endpoint, params=None, page_size=100):
        params = params or {}
        params.update({"customerId": CUSTOMER_ID, "source": "JDEAR", "role": "DXP_GPS_Role_Client"})
        all_rows = []
        offset = 0
        while True:
            p = dict(params, offset=offset, limit=page_size)
            qs = "&".join(f"{k}={v}" for k, v in p.items())
            url = f"{API_BASE}{endpoint}?{qs}"
            try:
                r = page.context.request.get(url, headers=headers_to_use, timeout=30000)
                if r.status != 200:
                    print(f"    [!] {r.status} en offset={offset}: {r.text()[:120]}")
                    break
                j = r.json()
            except Exception as e:
                print(f"    [!] err offset={offset}: {e}")
                break
            data = j.get("movements") or j.get("data") or j.get("items") or j.get("content") or j.get("results") or j.get("documents") or (j if isinstance(j, list) else None)
            if data is None:
                print(f"    [.] estructura no reconocida, keys={list(j.keys())[:8] if isinstance(j, dict) else type(j).__name__}")
                break
            # Capturar total del response (puede estar en metadata)
            meta = j.get("metadata") or {}
            tot = meta.get("total") or meta.get("totalElements") or meta.get("count") or j.get("total")
            if not data: break
            all_rows.extend(data)
            print(f"    offset={offset:>5} got={len(data):>4} acumulado={len(all_rows):>5}" + (f" / total={tot}" if tot else ""), flush=True)
            offset += page_size
            if tot and len(all_rows) >= tot: break
            if len(data) < page_size: break
            time.sleep(0.3)
        return all_rows

    print(f"\n[+] Bajando MOVEMENTS...", flush=True)
    movs = fetch_paged("/v1/movements", {"sortBy":"loadUnloadDate","sort":"desc","legalDocument":""}, 200)
    (DATA/"movements.json").write_text(json.dumps(movs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {len(movs)} movimientos guardados")
    if movs:
        first = movs[0]
        if isinstance(first, dict):
            print(f"  Cols: {list(first.keys())}")
            print(f"  Sample fila 1 (campos no vacios):")
            for k, v in first.items():
                if v not in (None, "", 0, "0", "0.0"):
                    print(f"    {k:<40} = {repr(v)[:80]}")
        else:
            print(f"  Tipo de fila: {type(first).__name__} | Sample: {repr(first)[:200]}")

    print(f"\n[+] Probando endpoints documentos/cobros...", flush=True)
    for ep_path, ep_name in [
        ("/v1/documents", "documents"),
        ("/v1/charges", "charges"),
        ("/v1/payments", "payments"),
        ("/v1/myaccount/documents", "myaccount_documents"),
        ("/v1/invoices", "invoices"),
        ("/v1/invoicesandliquidations", "invoicesandliquidations"),
    ]:
        url = f"{API_BASE}{ep_path}?customerId={CUSTOMER_ID}&source=JDEAR&role=DXP_GPS_Role_Client&offset=0&limit=5"
        try:
            r = page.context.request.get(url, headers=headers_to_use, timeout=15000)
            if r.status == 200:
                j = r.json()
                size = len(j) if isinstance(j,list) else len(j.get("data",[]) or j.get("items",[]) or j.get("content",[]))
                print(f"  ✓ {ep_path:<40} {r.status} | items={size}")
                if size > 0:
                    sample = j if isinstance(j,list) else (j.get("data") or j.get("items") or j.get("content"))
                    print(f"      cols: {list(sample[0].keys())[:30]}")
            else:
                print(f"  ✗ {ep_path:<40} {r.status}: {r.text()[:80]}")
        except Exception as e:
            print(f"  ✗ {ep_path:<40} ERR {str(e)[:60]}")

    ctx.close()
print("\n[+] Done.")
