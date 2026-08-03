"""Sniff de red en Cargill /Movements: ver qué endpoints internos sirven la tabla."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
OUT.mkdir(parents=True, exist_ok=True)

CALLS = []

def on_response(resp):
    try:
        url = resp.url
        method = resp.request.method
        ct = resp.headers.get("content-type","")
        if "json" in ct.lower() or url.endswith(".json"):
            try:
                body = resp.text()
                CALLS.append({"url": url, "method": method, "status": resp.status,
                               "size": len(body),
                               "preview": body[:300]})
                print(f"  📡 [{resp.status}] {method} {url[:120]} ({len(body)}b)", flush=True)
            except: pass
    except: pass

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("response", on_response)

    print("[+] Navegando a /Movements... esperando 12s para que cargue la tabla y se hagan TODAS las llamadas", flush=True)
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(12000)
    print(f"\n[+] {len(CALLS)} llamadas JSON capturadas")
    print(f"[+] URL final: {page.url}")

    # Guardar todas las llamadas JSON detectadas
    (OUT/"cargill_movements_apis.json").write_text(
        json.dumps([{k:v for k,v in c.items() if k != "preview"} | {"preview": c["preview"]} for c in CALLS],
                   indent=2, ensure_ascii=False)[:200_000],
        encoding="utf-8")
    print(f"[+] Guardado en cargill_movements_apis.json")

    # Imprimir las más interesantes (con data en el preview)
    print(f"\n[+] APIs con data util (preview JSON):")
    for c in CALLS:
        if c["status"] == 200 and c["size"] > 200 and ("[" in c["preview"][:10] or '"data"' in c["preview"] or '"rows"' in c["preview"] or '"items"' in c["preview"]):
            print(f"\n  📦 {c['method']} {c['url']}")
            print(f"     {c['preview'][:300]}")

    print(f"\n[+] Done. Cerrá el browser para continuar.")
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
