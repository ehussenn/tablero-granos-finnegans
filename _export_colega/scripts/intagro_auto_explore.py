"""Intagro auto-explorer: login + sniff."""
from __future__ import annotations
import sys, os, json, time, threading
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".intagro_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "intagro"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("INTAGRO_USER", "agronasaja")
PWD = os.environ.get("INTAGRO_PASS", "<PASSWORD>")

REQ_LOG = []; RESP_LOG = []; TOKEN = [None]; LOCK = threading.Lock()
EXCLUDE = (".css", ".js", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".svg", ".ico", ".gif",
            "fonts.gstatic", "analytics", "googletag", "doubleclick", "facebook.net", "hotjar")

def on_req(r):
    try:
        url = r.url
        if any(x in url.lower() for x in EXCLUDE): return
        a = r.headers.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a; print(f"  🔑 Bearer", flush=True)
        body = ""
        try:
            if r.method in ("POST","PUT"): body = r.post_data or ""
        except: pass
        with LOCK:
            REQ_LOG.append({"url": url, "method": r.method, "auth": bool(a),
                             "post_body": body[:1500], "ts": time.time()})
    except: pass

def on_response(resp):
    try:
        url = resp.url
        if any(x in url.lower() for x in EXCLUDE): return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct.lower() and "xml" not in ct.lower(): return
        try: body = resp.text()
        except: return
        with LOCK:
            RESP_LOG.append({"url": url, "method": resp.request.method, "status": resp.status,
                              "size": len(body), "preview": body[:800], "ts": time.time()})
    except: pass

def write_logs():
    while True:
        try:
            time.sleep(5)
            with LOCK: reqs = list(REQ_LOG); resps = list(RESP_LOG); tok = TOKEN[0]
            (OUT/"requests.json").write_text(json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT/"responses.json").write_text(json.dumps(resps, ensure_ascii=False, indent=2), encoding="utf-8")
            if tok: (OUT/"token.txt").write_text(tok, encoding="utf-8")
        except: pass

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req); page.on("response", on_response)
    threading.Thread(target=write_logs, daemon=True).start()

    print("[+] Goto https://portal.intagro.com/")
    page.goto("https://portal.intagro.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    page.screenshot(path=str(OUT/"01_landing.png"), full_page=True)
    print(f"    URL: {page.url}")

    # Login
    if page.locator("input[type='password']").count() > 0:
        print("[+] Login...")
        try:
            for u_sel in ["input[name='username']", "input[name='usuario']", "input[type='text']",
                          "input[type='email']", "input[placeholder*='usuario' i]"]:
                el = page.locator(u_sel).first
                if el.count() > 0:
                    el.fill(USER, timeout=4000); print(f"  user OK ({u_sel})"); break
            page.locator("input[type='password']").first.fill(PWD, timeout=4000); print(f"  pass OK")
            for b_sel in ["button:has-text('Ingresar')", "button:has-text('Iniciar sesión')",
                          "button[type='submit']", "input[type='submit']",
                          "button:has-text('Acceder')", "button:has-text('Entrar')"]:
                el = page.locator(b_sel).first
                if el.count() > 0:
                    el.click(timeout=4000); print(f"  submit ({b_sel})"); break
            page.wait_for_timeout(15000)
        except Exception as e: print(f"  [!] {e}")
    page.screenshot(path=str(OUT/"02_after_login.png"), full_page=True)
    print(f"[+] URL post-login: {page.url}")

    # Enumerar menú
    page.wait_for_timeout(3000)
    links = page.locator("a, button").all()
    labels = []
    for el in links[:300]:
        try:
            txt = el.inner_text(timeout=300).strip()
            if txt and 2 < len(txt) < 50:
                href = ""
                try: href = el.get_attribute("href") or ""
                except: pass
                labels.append({"text": txt, "href": href})
        except: pass
    seen = set(); unique = []
    for l in labels:
        k = (l["text"].lower(), l["href"])
        if k in seen: continue
        seen.add(k); unique.append(l)
    (OUT/"menu_links.json").write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] {len(unique)} labels:")
    for l in unique[:60]:
        print(f"    [{l['text'][:32]:32s}] -> {l['href'][:90]}")

    # Visitar secciones
    targets = ["Contratos","Liquidaciones","Fijaciones","Aplicaciones","Movimientos","Descargas",
               "Mercadería","Operaciones","Saldos","Cuenta corriente","Negociaciones",
               "Calidad","Pagos","Facturas","Resumen","Inicio","Granos","Boletos","Posición"]
    print(f"\n[+] Visitando secciones...")
    for w in targets:
        try:
            sel = f"a:has-text('{w}'), button:has-text('{w}'), [role='menuitem']:has-text('{w}'), li:has-text('{w}') a"
            cnt = page.locator(sel).count()
            if cnt > 0:
                print(f"  → {w} ({cnt})")
                page.locator(sel).first.click(timeout=4000)
                page.wait_for_timeout(8000)
                fn = w.lower().replace(" ","_").replace("í","i").replace("ó","o").replace("á","a")
                page.screenshot(path=str(OUT/f"sec_{fn}.png"), full_page=True)
        except Exception as e: print(f"    [!] {w}: {str(e)[:60]}")

    with LOCK:
        (OUT/"requests.json").write_text(json.dumps(REQ_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT/"responses.json").write_text(json.dumps(RESP_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] {len(REQ_LOG)} reqs / {len(RESP_LOG)} json / token={'SI' if TOKEN[0] else 'NO'}")
    page.wait_for_timeout(60000)
    ctx.close()
