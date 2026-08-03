"""COFCO auto-explorer: login SAP IAS + Fiori launchpad sniff."""
from __future__ import annotations
import sys, os, json, time, threading
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".cofco_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "cofco"
OUT.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("COFCO_USER", "mloza@agronasaja.com.ar")
PWD = os.environ.get("COFCO_PASS", "mATUTE2023&")
PORTAL = "https://cofco-partner-productive.launchpad.cfapps.us21.hana.ondemand.com/"

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

    print(f"[+] Goto {PORTAL}")
    page.goto(PORTAL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)
    page.screenshot(path=str(OUT/"01_landing.png"), full_page=True)
    print(f"    URL: {page.url}")

    # SAP IAS Login: email Y password en la MISMA pantalla, después click Continuar UNA vez
    if "accounts.cloud.sap" in page.url or page.locator("input[type='password']").count() > 0:
        print("[+] SAP IAS login (typing letra por letra)...")
        try:
            # Click + type email
            for u_sel in ["input[type='email']", "input[name='username']", "input[name='email']", "input[type='text']"]:
                el = page.locator(u_sel).first
                if el.count() > 0:
                    el.click(timeout=4000)
                    el.fill("", timeout=2000)  # clear primero
                    el.type(USER, delay=50, timeout=8000)
                    print(f"  user typed ({u_sel})"); break
            # Click + type password
            pwd_el = page.locator("input[type='password']").first
            pwd_el.click(timeout=4000)
            pwd_el.fill("", timeout=2000)
            pwd_el.type(PWD, delay=50, timeout=8000)
            print(f"  pass typed")
            page.wait_for_timeout(1500)
            # Screenshot DEBUG ANTES del click
            page.screenshot(path=str(OUT/"02b_before_submit.png"), full_page=True)
            # Verificar que el password tenga valor
            try:
                pwd_value_len = page.evaluate("document.querySelector('input[type=\"password\"]').value.length")
                print(f"  pwd_value_len = {pwd_value_len}")
            except: pass
            # Submit
            for b_sel in ["button:has-text('Continuar')", "button:has-text('Continue')",
                          "button[type='submit']", "button:has-text('Iniciar')",
                          "button:has-text('Sign In')"]:
                el = page.locator(b_sel).first
                if el.count() > 0:
                    el.click(timeout=4000); print(f"  submit ({b_sel})"); break
            page.wait_for_timeout(20000)
        except Exception as e: print(f"  [!] {e}")

    page.screenshot(path=str(OUT/"02_after_login.png"), full_page=True)
    print(f"\n[+] URL post-login: {page.url}")

    # Esperar que cargue Fiori launchpad
    print("[+] Esperando carga del launchpad...")
    page.wait_for_timeout(15000)
    page.screenshot(path=str(OUT/"03_launchpad.png"), full_page=True)

    # Enumerar tiles
    page.wait_for_timeout(3000)
    links = page.locator("a, button, [role='button'], .sapMTile").all()
    labels = []
    for el in links[:300]:
        try:
            txt = el.inner_text(timeout=300).strip()
            if txt and 2 < len(txt) < 80:
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
    print(f"\n[+] {len(unique)} elementos en launchpad:")
    for l in unique[:60]:
        print(f"    [{l['text'][:50]:50s}] -> {l['href'][:80]}")

    # Esperar más para que el usuario vea
    print("\n[+] Esperando 90s para que cliquees alguna tile si querés...")
    page.wait_for_timeout(90000)

    with LOCK:
        (OUT/"requests.json").write_text(json.dumps(REQ_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT/"responses.json").write_text(json.dumps(RESP_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] {len(REQ_LOG)} reqs / {len(RESP_LOG)} json / token={'SI' if TOKEN[0] else 'NO'}")
    ctx.close()
