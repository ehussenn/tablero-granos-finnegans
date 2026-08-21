"""LDC webportal — sniffer continuo. Escribe logs cada 5s mientras navegás.
Usa el perfil persistente — si ya estás logueado, no pide login otra vez."""
from __future__ import annotations
import sys, os, json, time, threading
from pathlib import Path
from _env import need

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

USER = need("LDC_USER")
PWD = need("LDC_PASS")

REQ_LOG = []
RESP_LOG = []
TOKEN = [None]
LOCK = threading.Lock()

EXCLUDE_EXT = (".css", ".js", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".svg", ".ico", ".gif", ".ttf")

def on_request(r):
    try:
        url = r.url
        if any(url.endswith(x) or x+"?" in url for x in EXCLUDE_EXT): return
        if "google" in url or "analytics" in url or "fonts.gstatic" in url: return
        h = dict(r.headers)
        auth = h.get("authorization", "")
        if auth and "bearer" in auth.lower() and not TOKEN[0]:
            TOKEN[0] = auth
            print(f"  🔑 Bearer capturado", flush=True)
        with LOCK:
            REQ_LOG.append({"url": url, "method": r.method, "ts": time.time(),
                             "auth_present": bool(auth)})
    except: pass

def on_response(resp):
    try:
        url = resp.url
        if any(url.endswith(x) or x+"?" in url for x in EXCLUDE_EXT): return
        if "google" in url or "analytics" in url: return
        if resp.request.method not in ("GET", "POST"): return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct.lower(): return
        try:
            body = resp.text()
        except: return
        with LOCK:
            RESP_LOG.append({"url": url, "method": resp.request.method,
                              "status": resp.status, "size": len(body),
                              "preview": body[:500],
                              "ts": time.time()})
    except: pass

def write_logs():
    """Volcar a disco cada 5s en hilo background."""
    while True:
        try:
            time.sleep(5)
            with LOCK:
                reqs = list(REQ_LOG)
                resps = list(RESP_LOG)
                tok = TOKEN[0]
            (OUT/"requests.json").write_text(
                json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT/"responses.json").write_text(
                json.dumps(resps, ensure_ascii=False, indent=2), encoding="utf-8")
            if tok:
                (OUT/"token.txt").write_text(tok, encoding="utf-8")
            # Resumen rápido
            paths = {}
            for r in resps:
                p = r["url"].split("?")[0]
                paths.setdefault(p, 0)
                paths[p] += 1
            top = sorted(paths.items(), key=lambda x:-x[1])[:6]
            top_str = " · ".join(f"{c}x {p.split('/api/')[-1][:40]}" for p,c in top)
            print(f"  [logs] {len(reqs)} req | {len(resps)} json | top: {top_str}", flush=True)
        except Exception as e:
            print(f"  [!] writer err: {e}", flush=True)

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    print("[+] Abriendo https://mildc.com/webportal")
    page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    # Login si hace falta
    if "login" in page.url.lower() or page.locator("input[type='password']").count() > 0:
        print("[+] Intentando login...")
        try:
            page.locator("input[type='text'], input[type='email']").first.fill(USER, timeout=3000)
            page.locator("input[type='password']").first.fill(PWD, timeout=3000)
            page.locator("button:has-text('Iniciar sesión'), button[type='submit']").first.click(timeout=3000)
            page.wait_for_timeout(10000)
        except Exception as e:
            print(f"    [!] login err: {e}")
    print(f"[+] URL: {page.url}")
    print(f"\n[+] NAVEGÁ AHORA por: Contratos → Liquidaciones → Fijaciones → Aplicaciones → Movimientos")
    print(f"[+] Buscá algo por CTG si podés. Cuando termines de explorar, cerrá el browser.")
    print(f"[+] Logs en {OUT} se actualizan cada 5s.\n")

    # Hilo escritor
    threading.Thread(target=write_logs, daemon=True).start()

    # Esperar hasta que cierres el browser
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(3000)
    except: pass

    # Final flush
    with LOCK:
        (OUT/"requests.json").write_text(json.dumps(REQ_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT/"responses.json").write_text(json.dumps(RESP_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] Final: {len(REQ_LOG)} reqs / {len(RESP_LOG)} json responses")
    ctx.close()
