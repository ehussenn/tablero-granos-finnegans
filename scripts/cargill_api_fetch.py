"""Llama directamente a la API interna de Cargill (api.cglcloud.com) usando sesión Playwright.
Pagina todo y guarda JSON crudo. Mucho más rápido que parsear HTML."""
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
DATA = ROOT.parent / "data" / "cargill"
DATA.mkdir(parents=True, exist_ok=True)

CUSTOMER_ID = "35188546"  # Agronasaja (detectado del sniff anterior)
API_BASE = "https://api.cglcloud.com/api/dxo/gps"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,  # primera vez visible por las dudas
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    # Navegar a /Movements para que la SPA cargue el token Bearer y lo deje en memoria
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print("[+] Cargando /Movements para activar sesión + token...", flush=True)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)  # esperar que el JS haga sus llamadas y popule el token
    print(f"    URL: {page.url}", flush=True)

    def fetch_paged(endpoint, params=None, page_size=100):
        """Pagina via offset/limit. Usa fetch DESDE el browser (lleva el token Bearer)."""
        params = params or {}
        params.update({"customerId": CUSTOMER_ID, "source": "JDEAR", "role": "DXP_GPS_Role_Client"})
        all_rows = []
        offset = 0
        while True:
            p = dict(params, offset=offset, limit=page_size)
            qs = "&".join(f"{k}={v}" for k, v in p.items())
            url = f"{API_BASE}{endpoint}?{qs}"
            try:
                # Fetch desde el contexto del browser — incluye los headers Bearer automatizados
                result = page.evaluate("""async (url) => {
                    try {
                        const r = await fetch(url);
                        const txt = await r.text();
                        return { status: r.status, body: txt };
                    } catch(e) { return { error: e.message }; }
                }""", url)
                if result.get("error"):
                    print(f"    [!] err offset={offset}: {result['error']}")
                    break
                if result["status"] != 200:
                    print(f"    [!] {result['status']} en offset={offset}: {result['body'][:120]}")
                    break
                j = json.loads(result["body"])
            except Exception as e:
                print(f"    [!] err offset={offset}: {e}")
                break
            data = j.get("data") or j.get("items") or j.get("content") or j.get("results") or (j if isinstance(j, list) else None)
            if data is None:
                print(f"    [.] estructura no reconocida en offset={offset}, keys={list(j.keys())[:8] if isinstance(j, dict) else type(j).__name__}")
                if isinstance(j, dict): all_rows.append(j)
                break
            if not data: break
            all_rows.extend(data)
            tot = j.get("total") or j.get("totalElements") or j.get("count") or None
            print(f"    offset={offset:>5} got={len(data):>4} acumulado={len(all_rows):>5}" + (f" / total={tot}" if tot else ""), flush=True)
            offset += page_size
            if tot and len(all_rows) >= tot: break
            if len(data) < page_size: break
            time.sleep(0.3)
        return all_rows

    # 1) MOVIMIENTOS / DESCARGAS
    print("\n[+] Bajando todos los MOVEMENTS (descargas físicas)...", flush=True)
    movs = fetch_paged("/v1/movements", {"sortBy": "loadUnloadDate", "sort": "desc", "legalDocument": ""}, page_size=100)
    (DATA/"movements.json").write_text(json.dumps(movs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {len(movs)} movimientos guardados en data/cargill/movements.json")
    if movs:
        print(f"  Columns sample: {list(movs[0].keys())[:30]}")
        print(f"  Primera fila:")
        for k, v in movs[0].items():
            print(f"    {k:<40} = {repr(v)[:60]}")

    # 2) Probar también endpoints de documentos/facturas con eval
    print(f"\n[+] Probando endpoints DOCUMENTS...", flush=True)
    for ep in ["/v1/documents", "/v1/invoices", "/v1/invoicesandliquidations", "/v1/myaccount/documents",
                "/v2/documents", "/v2/invoices", "/v1/charges", "/v1/payments"]:
        url = f"{API_BASE}{ep}?customerId={CUSTOMER_ID}&source=JDEAR&role=DXP_GPS_Role_Client&offset=0&limit=5"
        result = page.evaluate("""async (url) => {
            try { const r = await fetch(url); return { status: r.status, body: (await r.text()).slice(0, 300) }; }
            catch(e) { return { error: e.message }; }
        }""", url)
        if result.get("error"):
            print(f"    {ep:<40} -> ERR {result['error'][:60]}")
        else:
            print(f"    {ep:<40} -> {result['status']}  preview: {result['body'][:80]}")

    ctx.close()
print("\n[+] Done.")
