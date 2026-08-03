"""Navega a /webportal/aplicaciones y captura el payload exacto que carga la lista."""
import sys, os, json, time, threading
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "ldc"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("LDC_USER", "<EXTRANET_USER_EMAIL>")
PWD = os.environ.get("LDC_PASS", "<PASSWORD>")
LOG = []; TOKEN = [None]; LOCK = threading.Lock()
EXCLUDE = (".css", ".js", ".png", ".jpg", ".woff", ".svg", ".ico", "google", "analytics")

def on_req(r):
    try:
        if any(x in r.url.lower() for x in EXCLUDE): return
        h = dict(r.headers); a = h.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a
        body = ""
        try: body = r.post_data or ""
        except: pass
        with LOCK:
            LOG.append({"url": r.url, "method": r.method, "body": body[:2000], "ts": time.time()})
    except: pass

def writer():
    while True:
        try:
            time.sleep(3)
            with LOCK: l = list(LOG)
            (OUT/"sniff_apps.json").write_text(json.dumps(l, ensure_ascii=False, indent=2), encoding="utf-8")
        except: pass

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req)
    threading.Thread(target=writer, daemon=True).start()

    page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if page.locator("input[type='password']").count() > 0:
        page.locator("input[type='text']").first.fill(USER)
        page.locator("input[type='password']").first.fill(PWD)
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(12000)

    # Navegar a aplicaciones
    print("[+] Goto /webportal/aplicaciones")
    page.goto("https://mildc.com/webportal/aplicaciones", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(12000)
    page.screenshot(path=str(OUT/"apps_page.png"), full_page=True)

    # Intentar clickear botón buscar (o lo que sea que dispara la query)
    print("[+] Buscando botón Buscar/Aplicar...")
    for txt in ["Buscar", "Aplicar filtro", "Filtrar", "Consultar", "Ver", "Search"]:
        try:
            el = page.locator(f"button:has-text('{txt}')").first
            if el.count() > 0:
                el.click(timeout=3000)
                print(f"  click {txt}")
                page.wait_for_timeout(8000)
                break
        except: pass

    page.wait_for_timeout(5000)
    page.screenshot(path=str(OUT/"apps_after_search.png"), full_page=True)

    # Liquidaciones
    print("\n[+] Goto /webportal/liquidaciones")
    page.goto("https://mildc.com/webportal/liquidaciones", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(10000)
    page.screenshot(path=str(OUT/"liq_page.png"), full_page=True)

    # Final
    with LOCK:
        (OUT/"sniff_apps.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] {len(LOG)} requests capturadas")
    print(f"[+] Out: {OUT}/sniff_apps.json")
    page.wait_for_timeout(15000)
    ctx.close()
