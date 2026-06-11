"""LDC auto-explorer: hace login, navega por TODOS los menúes principales,
captura cada endpoint API + screenshots. Después podemos armar el scraper definitivo."""
from __future__ import annotations
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

USER = os.environ.get("LDC_USER", "pgauto@agronasaja.com.ar")
PWD = os.environ.get("LDC_PASS", "Nasaja1234.")

REQ_LOG = []
RESP_LOG = []
TOKEN = [None]
LOCK = threading.Lock()
EXCLUDE = (".css", ".js", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".svg", ".ico", ".gif", ".ttf", "fonts.gstatic", "analytics", "googletag")

def on_request(r):
    try:
        url = r.url
        if any(x in url.lower() for x in EXCLUDE): return
        h = dict(r.headers)
        auth = h.get("authorization", "")
        if auth and "bearer" in auth.lower() and not TOKEN[0]:
            TOKEN[0] = auth
            print(f"  🔑 Bearer capturado", flush=True)
        body = ""
        try:
            if r.method == "POST": body = r.post_data or ""
        except: pass
        with LOCK:
            REQ_LOG.append({"url": url, "method": r.method, "auth": bool(auth),
                             "post_body": body[:500] if body else "",
                             "ts": time.time()})
    except: pass

def on_response(resp):
    try:
        url = resp.url
        if any(x in url.lower() for x in EXCLUDE): return
        if resp.request.method not in ("GET", "POST"): return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct.lower() and "xml" not in ct.lower(): return
        try: body = resp.text()
        except: return
        with LOCK:
            RESP_LOG.append({"url": url, "method": resp.request.method, "status": resp.status,
                              "size": len(body), "preview": body[:600], "ts": time.time()})
    except: pass

def write_logs():
    while True:
        try:
            time.sleep(5)
            with LOCK:
                reqs = list(REQ_LOG); resps = list(RESP_LOG); tok = TOKEN[0]
            (OUT/"requests.json").write_text(json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT/"responses.json").write_text(json.dumps(resps, ensure_ascii=False, indent=2), encoding="utf-8")
            if tok: (OUT/"token.txt").write_text(tok, encoding="utf-8")
        except Exception as e: print(f"  [!] writer: {e}", flush=True)

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
    threading.Thread(target=write_logs, daemon=True).start()

    print("[+] Goto https://mildc.com/webportal")
    page.goto("https://mildc.com/webportal", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    # Login si hace falta
    if page.locator("input[type='password']").count() > 0:
        print("[+] Login...")
        try:
            page.locator("input[type='text'], input[type='email']").first.fill(USER, timeout=3000)
            page.locator("input[type='password']").first.fill(PWD, timeout=3000)
            page.locator("button:has-text('Iniciar sesión'), button[type='submit']").first.click(timeout=3000)
            page.wait_for_timeout(10000)
        except Exception as e: print(f"  [!] {e}")
    print(f"[+] URL: {page.url}")
    page.screenshot(path=str(OUT/"02_home.png"), full_page=True)

    # Enumerar links del menú top
    print("\n[+] Buscando links de menú...")
    try:
        page.wait_for_timeout(3000)
        # Probar varios selectores tipo menú
        links = page.locator("a, button").all()
        print(f"  encontrados {len(links)} elementos clickeables")
        labels = []
        for el in links[:200]:
            try:
                txt = el.inner_text(timeout=500).strip()
                if txt and len(txt) < 50 and len(txt) > 2:
                    href = ""
                    try: href = el.get_attribute("href") or ""
                    except: pass
                    labels.append({"text": txt, "href": href})
            except: pass
        # Únicos
        seen = set(); unique = []
        for l in labels:
            k = (l["text"].lower(), l["href"])
            if k in seen: continue
            seen.add(k); unique.append(l)
        (OUT/"menu_links.json").write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {len(unique)} labels únicos:")
        for l in unique[:40]:
            print(f"    [{l['text'][:30]:30s}] -> {l['href'][:80]}")
    except Exception as e: print(f"  [!] enum err: {e}")

    # Intentar visitar cada sección
    target_words = ["Contratos", "Liquidaciones", "Fijaciones", "Aplicaciones", "Movimientos",
                    "Descargas", "Cargas", "Mercaderia", "Mercadería", "Operaciones", "Saldos", "Inicio"]
    print(f"\n[+] Visitando secciones automáticamente...")
    for w in target_words:
        try:
            sel = f"a:has-text('{w}'), button:has-text('{w}'), [role='menuitem']:has-text('{w}')"
            cnt = page.locator(sel).count()
            if cnt > 0:
                print(f"  → click {w} ({cnt} matches)")
                page.locator(sel).first.click(timeout=4000)
                page.wait_for_timeout(8000)
                fn = w.lower().replace(" ","_").replace("í","i").replace("ó","o").replace("á","a")
                page.screenshot(path=str(OUT/f"sec_{fn}.png"), full_page=True)
        except Exception as e:
            print(f"    [!] {w}: {str(e)[:80]}")

    # Final flush
    with LOCK:
        (OUT/"requests.json").write_text(json.dumps(REQ_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT/"responses.json").write_text(json.dumps(RESP_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] {len(REQ_LOG)} reqs / {len(RESP_LOG)} json responses")
    print(f"[+] Token: {'SI' if TOKEN[0] else 'NO'}")
    print(f"[+] Out: {OUT}")

    # Dejarlo abierto para inspeccion manual
    print(f"\n[+] Mantengo el browser abierto 60s mas...")
    page.wait_for_timeout(60000)
    ctx.close()
