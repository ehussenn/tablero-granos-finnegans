"""Click en una liquidacion y aplicacion y capturar las requests del detalle."""
import sys, os, json, time, threading
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "ldc"

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

LOG = []; TOKEN = [None]; LOCK = threading.Lock()
EXCLUDE = (".css", ".js", ".png", ".jpg", ".woff", ".svg", ".ico", "google", "analytics")

def on_req(r):
    try:
        if any(x in r.url.lower() for x in EXCLUDE): return
        a = r.headers.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a
        body = ""
        try: body = r.post_data or ""
        except: pass
        with LOCK:
            LOG.append({"url": r.url, "method": r.method, "body": body[:2000], "ts": time.time()})
    except: pass

def on_resp(resp):
    try:
        if any(x in resp.url.lower() for x in EXCLUDE): return
        if "json" not in resp.headers.get("content-type", "").lower(): return
        try: body = resp.text()
        except: return
        with LOCK:
            # Anotar el ultimo response al log
            for r in reversed(LOG):
                if r["url"] == resp.url and r.get("method") == resp.request.method:
                    r["resp_status"] = resp.status
                    r["resp_preview"] = body[:1000]
                    break
    except: pass

def writer():
    while True:
        try:
            time.sleep(3)
            with LOCK: l = list(LOG)
            (OUT/"sniff_detail.json").write_text(json.dumps(l, ensure_ascii=False, indent=2), encoding="utf-8")
        except: pass

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req); page.on("response", on_resp)
    threading.Thread(target=writer, daemon=True).start()

    print("[+] Goto /webportal/liquidaciones")
    page.goto("https://mildc.com/webportal/liquidaciones", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(12000)
    page.screenshot(path=str(OUT/"detail_01_liq.png"), full_page=True)

    # Probar a clickear botón "Buscar" o filtro
    for sel in ["button:has-text('Buscar')", "button:has-text('Consultar')", "button:has-text('Aplicar')"]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                el.click(timeout=3000)
                print(f"  click {sel}")
                page.wait_for_timeout(8000)
                break
        except: pass

    page.wait_for_timeout(3000)
    page.screenshot(path=str(OUT/"detail_02_liq_after.png"), full_page=True)

    # Intentar clickear la primera fila
    print("[+] click primera fila...")
    for sel in ["tbody tr", "[class*='row']:not([class*='header'])", "div[class*='dx-row']:not([class*='header'])"]:
        try:
            els = page.locator(sel).all()
            if len(els) > 0:
                els[0].click(timeout=4000)
                print(f"  click row con sel: {sel}")
                page.wait_for_timeout(8000)
                break
        except: pass
    page.screenshot(path=str(OUT/"detail_03_liq_row.png"), full_page=True)

    # Aplicaciones
    print("\n[+] Goto /webportal/aplicaciones")
    page.goto("https://mildc.com/webportal/aplicaciones", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(10000)
    page.screenshot(path=str(OUT/"detail_10_apps.png"), full_page=True)

    # Probar varios botones
    for sel in ["button:has-text('Buscar')", "button:has-text('Consultar')",
                "button[type='submit']", "[class*='submit']", "[class*='search']"]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                el.click(timeout=3000)
                print(f"  click {sel}")
                page.wait_for_timeout(10000)
                page.screenshot(path=str(OUT/f"detail_11_apps_after.png"), full_page=True)
                break
        except: pass

    # Final flush
    with LOCK:
        (OUT/"sniff_detail.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] {len(LOG)} requests")
    page.wait_for_timeout(10000)
    ctx.close()
