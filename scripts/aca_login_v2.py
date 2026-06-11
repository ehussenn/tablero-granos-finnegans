"""ACA login v2 — clickear 'Identificarse' antes del fill."""
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
PWD = os.environ.get("ACA_PASS", "nasaja12345")

LOG = []; TOKEN = [None]; LOCK = threading.Lock()
EXCLUDE = (".css", ".js", ".png", ".jpg", ".woff", ".svg", ".ico", "fonts.gstatic", "analytics", "googletag")

def on_req(r):
    try:
        if any(x in r.url.lower() for x in EXCLUDE): return
        a = r.headers.get("authorization", "")
        if a and "bearer" in a.lower() and not TOKEN[0]: TOKEN[0] = a
        body = ""
        try: body = r.post_data or ""
        except: pass
        with LOCK:
            LOG.append({"url": r.url, "method": r.method, "body": body[:1500], "ts": time.time()})
    except: pass

def on_resp(resp):
    try:
        if any(x in resp.url.lower() for x in EXCLUDE): return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct.lower(): return
        try: body = resp.text()
        except: return
        with LOCK:
            for r in reversed(LOG):
                if r["url"] == resp.url and r.get("method") == resp.request.method:
                    r["resp_status"] = resp.status
                    r["resp"] = body[:2000]; break
    except: pass

def writer():
    while True:
        try:
            time.sleep(3)
            with LOCK: l = list(LOG)
            (OUT/"login_v2.json").write_text(json.dumps(l, ensure_ascii=False, indent=2), encoding="utf-8")
            if TOKEN[0]: (OUT/"token_v2.txt").write_text(TOKEN[0], encoding="utf-8")
        except: pass

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False, viewport={"width":1500,"height":950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("request", on_req); page.on("response", on_resp)
    threading.Thread(target=writer, daemon=True).start()

    page.goto("https://www.acabase.com.ar/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    page.screenshot(path=str(OUT/"v2_01_landing.png"), full_page=True)

    # Click "Identificarse" o "Ingresar" para abrir form
    print("[+] Click 'Identificarse'...")
    clicked = False
    for sel in ["a:has-text('Identificarse')", "button:has-text('Identificarse')",
                "a:has-text('Ingresar')", "button:has-text('Ingresar')",
                ".btn-login", "#identificarse", "a[href*='login']"]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                el.click(timeout=3000)
                print(f"  click sel: {sel}")
                clicked = True
                page.wait_for_timeout(4000)
                break
        except: pass
    page.screenshot(path=str(OUT/"v2_02_after_click.png"), full_page=True)

    # Ahora intentar fill
    print("[+] Fill usuario+pass...")
    try:
        for u_sel in ["#usuario", "input[name='xusuario']", "input[name='usuario']",
                      "input[type='text']", "input[placeholder*='Usuario' i]"]:
            try:
                el = page.locator(u_sel).first
                if el.count() > 0:
                    el.fill(USER, timeout=4000)
                    print(f"  usuario fill OK ({u_sel})")
                    break
            except: pass
        for p_sel in ["input[type='password']", "input[name='xclave']", "#clave", "input[name='password']"]:
            try:
                el = page.locator(p_sel).first
                if el.count() > 0:
                    el.fill(PWD, timeout=4000)
                    print(f"  pass fill OK ({p_sel})")
                    break
            except: pass
        for b_sel in ["button:has-text('Ingresar')", "button[type='submit']",
                      "input[type='submit']", "button:has-text('Entrar')",
                      "input[value='Ingresar']", "input[value*='ngresar']"]:
            try:
                el = page.locator(b_sel).first
                if el.count() > 0:
                    el.click(timeout=4000)
                    print(f"  submit OK ({b_sel})")
                    break
            except: pass
        page.wait_for_timeout(15000)
    except Exception as e:
        print(f"  [!] fill err: {e}")

    page.screenshot(path=str(OUT/"v2_03_after_login.png"), full_page=True)
    print(f"[+] URL post-login: {page.url}")

    # Si redirigió a otro subdominio, capturar
    if page.url != "https://www.acabase.com.ar/":
        print(f"[+] Cambio de URL detectado!")

    # Esperar a que cargue y explorar links
    page.wait_for_timeout(8000)
    page.screenshot(path=str(OUT/"v2_04_logged.png"), full_page=True)

    # Enumerar links nuevos
    links = page.locator("a").all()
    new_links = set()
    for el in links[:200]:
        try:
            href = el.get_attribute("href")
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                new_links.add(href)
        except: pass
    (OUT/"v2_links.json").write_text(json.dumps(sorted(new_links), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] {len(new_links)} links unicos en home logueada")
    for l in sorted(new_links)[:30]:
        print(f"   {l}")

    with LOCK:
        (OUT/"login_v2.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=2), encoding="utf-8")
        if TOKEN[0]: (OUT/"token_v2.txt").write_text(TOKEN[0], encoding="utf-8")
    print(f"\n[+] {len(LOG)} reqs / Token={'SI' if TOKEN[0] else 'NO'}")

    page.wait_for_timeout(30000)
    ctx.close()
