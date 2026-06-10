"""Cuando hace click en una descarga particular en Cargill, se hace una llamada API
adicional para traer el detalle (Análisis de Calidad + Servicios). Capturamos ese endpoint."""
import sys, json, time
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
        if "api.cglcloud.com" in url and resp.request.method == "GET":
            try:
                body = resp.text()
                CALLS.append({"url": url, "status": resp.status, "size": len(body), "body_preview": body[:200]})
                print(f"  📡 [{resp.status}] {url[:140]} ({len(body)}b)", flush=True)
            except: pass
    except: pass

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    print("[+] Pagina cargada, esperando que la tabla pinte...")
    # Cerrar popup si lo hay
    try:
        page.locator('div[role="dialog"] button').first.click(timeout=3000)
        page.wait_for_timeout(1000)
    except: pass
    page.wait_for_timeout(3000)

    # Capturar ahora — todos los requests siguientes son los del DETALLE
    page.on("response", on_response)

    # Click en la primera fila de descarga
    print("\n[+] Clickeando primera fila para abrir DETALLE...")
    try:
        # Probar selectores de filas de la tabla v2
        for sel in ['tbody tr', '[role="row"]:not([role="columnheader"])', '.MuiTableRow-root',
                     'tr[class*="row"]', 'div[class*="row"]:not([class*="header"])']:
            els = page.locator(sel).all()
            if els:
                # Click en el primero
                els[0].click(timeout=5000)
                print(f"    [+] Click con selector: {sel}")
                break
        page.wait_for_timeout(8000)  # esperar que cargue el detalle y haga sus llamadas
    except Exception as e:
        print(f"    [!] click error: {e}")

    page.screenshot(path=str(OUT/"cargill_detail_view.png"), full_page=True)
    print(f"\n[+] {len(CALLS)} requests API capturados durante el detalle:")
    for c in CALLS:
        print(f"   • {c['url'][:160]}")

    (OUT/"cargill_detail_apis.json").write_text(
        json.dumps([{**c, "body_preview":c["body_preview"][:200]} for c in CALLS], indent=2, ensure_ascii=False),
        encoding="utf-8")

    print(f"\n[+] Done. Cerrá el browser para terminar.")
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
