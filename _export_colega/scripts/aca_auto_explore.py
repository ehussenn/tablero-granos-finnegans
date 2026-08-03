"""ACA Base auto-explorer: login + navegar menúes + capturar API interna."""
from __future__ import annotations
import sys, os, json, time, threading
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".aca_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "aca"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("ACA_USER", "agronasaja")
PWD = os.environ.get("ACA_PASS", "<PASSWORD>")

REQ_LOG = []; RESP_LOG = []; TOKEN = [None]; LOCK = threading.Lock()
EXCLUDE = (".css", ".js", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".svg", ".ico", ".gif", ".ttf",
            "fonts.gstatic", "analytics", "googletag", "doubleclick")

def on_req(r):
    try:
        url = r.url
        if any(x in url.lower() for x in EXCLUDE): return
        h = dict(r.headers); auth = h.get("authorization", "")
        if auth and "bearer" in auth.lower() and not TOKEN[0]:
            TOKEN[0] = auth; print(f"  🔑 Bearer capturado", flush=True)
        body = ""
        try:
            if r.method == "POST": body = r.post_data or ""
        except: pass
        with LOCK:
            REQ_LOG.append({"url": url, "method": r.method, "auth": bool(auth),
                             "post_body": body[:1000], "ts": time.time()})
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
        except Exception as e: print(f"  [!] writer: {e}", flush=True)

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

    print("[+] Goto https://www.acabase.com.ar/")
    page.goto("https://www.acabase.com.ar/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    page.screenshot(path=str(OUT/"01_landing.png"), full_page=True)
    print(f"    URL: {page.url}")

    # Login
    if page.locator("input[type='password']").count() > 0:
        print("[+] Login...")
        try:
            # Probar campos típicos
            for u_sel in ["input[type='text']", "input[name='username']", "input[name='usuario']",
                          "input[type='email']", "input[placeholder*='usuario' i]"]:
                if page.locator(u_sel).first.count() > 0:
                    page.locator(u_sel).first.fill(USER, timeout=3000); break
            page.locator("input[type='password']").first.fill(PWD, timeout=3000)
            for b_sel in ["button[type='submit']", "input[type='submit']",
                          "button:has-text('Ingresar')", "button:has-text('Iniciar')",
                          "button:has-text('Login')", "button:has-text('Entrar')"]:
                if page.locator(b_sel).first.count() > 0:
                    page.locator(b_sel).first.click(timeout=3000); break
            page.wait_for_timeout(12000)
        except Exception as e: print(f"  [!] login err: {e}")
    page.screenshot(path=str(OUT/"02_after_login.png"), full_page=True)
    print(f"[+] URL post-login: {page.url}")

    # Enumerar links menú
    print("\n[+] Enumerando menú...")
    try:
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
        print(f"  {len(unique)} labels únicos:")
        for l in unique[:50]:
            print(f"    [{l['text'][:30]:30s}] -> {l['href'][:80]}")
    except Exception as e: print(f"  [!] enum: {e}")

    # Visitar secciones
    targets = ["Contratos","Liquidaciones","Fijaciones","Aplicaciones","Movimientos","Descargas",
               "Cargas","Mercadería","Mercaderia","Operaciones","Saldos","Cuenta corriente",
               "Cartas de porte","Carta de porte","CP","CTG","Análisis","Calidad","Pagos","Facturas"]
    print(f"\n[+] Visitando secciones automáticamente...")
    for w in targets:
        try:
            sel = f"a:has-text('{w}'), button:has-text('{w}'), [role='menuitem']:has-text('{w}'), li:has-text('{w}') a"
            cnt = page.locator(sel).count()
            if cnt > 0:
                print(f"  → click {w} ({cnt})")
                page.locator(sel).first.click(timeout=4000)
                page.wait_for_timeout(8000)
                fn = w.lower().replace(" ","_").replace("í","i").replace("ó","o").replace("á","a")
                page.screenshot(path=str(OUT/f"sec_{fn}.png"), full_page=True)
        except Exception as e: print(f"    [!] {w}: {str(e)[:80]}")

    # Final flush
    with LOCK:
        (OUT/"requests.json").write_text(json.dumps(REQ_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT/"responses.json").write_text(json.dumps(RESP_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] {len(REQ_LOG)} reqs / {len(RESP_LOG)} json responses")
    print(f"[+] Token: {'SI' if TOKEN[0] else 'NO'}")
    print(f"[+] Out: {OUT}")

    print(f"\n[+] Mantengo browser 60s mas...")
    page.wait_for_timeout(60000)
    ctx.close()
