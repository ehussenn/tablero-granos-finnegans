"""LDC webportal — login + sniffer de API interna.
Captura: Bearer token, todos los endpoints llamados, screenshots de cada sección."""
from __future__ import annotations
import sys, os, json, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".ldc_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "ldc"
OUT.mkdir(parents=True, exist_ok=True)

# .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("LDC_USER", "pgauto@agronasaja.com.ar")
PWD = os.environ.get("LDC_PASS", "Nasaja1234.")

# Captura todas las requests salientes y responses
REQ_LOG = []
RESP_LOG = []
TOKEN = [None]

def on_request(r):
    try:
        # ignorar tráfico estático
        if any(x in r.url for x in [".css", ".js", ".png", ".jpg", ".woff", ".svg", ".ico", ".gif"]):
            return
        h = dict(r.headers)
        auth = h.get("authorization", "")
        if auth and "bearer" in auth.lower() and not TOKEN[0]:
            TOKEN[0] = auth
            print(f"  🔑 Bearer capturado en {r.url[:80]}", flush=True)
        REQ_LOG.append({
            "url": r.url, "method": r.method,
            "auth": auth[:30] + "..." if auth else "",
            "ts": time.time(),
        })
    except: pass

def on_response(resp):
    try:
        if any(x in resp.url for x in [".css", ".js", ".png", ".jpg", ".woff", ".svg", ".ico", ".gif"]):
            return
        if resp.request.method == "GET" and resp.status == 200:
            try:
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    body = resp.text()
                    RESP_LOG.append({
                        "url": resp.url, "status": resp.status,
                        "size": len(body), "preview": body[:300],
                    })
            except: pass
    except: pass

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
    page.screenshot(path=str(OUT/"01_landing.png"), full_page=True)
    print(f"    URL: {page.url}")

    # Intentar login si vemos campos
    print("[+] Intentando login...")
    login_done = False
    for user_sel in ["input[name='username']", "input[name='email']", "input[type='email']",
                       "input[type='text']", "input[placeholder*='correo' i]", "input[placeholder*='usuario' i]"]:
        try:
            el = page.locator(user_sel).first
            if el.count() > 0:
                el.fill(USER, timeout=3000)
                print(f"    user fill OK ({user_sel})")
                break
        except: pass
    for pwd_sel in ["input[name='password']", "input[type='password']", "input[placeholder*='contraseña' i]"]:
        try:
            el = page.locator(pwd_sel).first
            if el.count() > 0:
                el.fill(PWD, timeout=3000)
                print(f"    pass fill OK ({pwd_sel})")
                break
        except: pass
    for btn_sel in ["button:has-text('Iniciar sesión')", "button[type='submit']",
                     "input[type='submit']", "button:has-text('Login')", "button:has-text('Ingresar')"]:
        try:
            el = page.locator(btn_sel).first
            if el.count() > 0:
                el.click(timeout=3000)
                print(f"    click submit ({btn_sel})")
                login_done = True
                break
        except: pass
    if not login_done:
        print("    [!] no encontré el botón submit — login manual requerido")
    page.wait_for_timeout(15000)
    page.screenshot(path=str(OUT/"02_after_login.png"), full_page=True)
    print(f"[+] URL post-login: {page.url}")

    # Explorar el menú: clickear cada item top-level que se vea
    print("\n[+] Esperando 30s para que cargues alguna sección manualmente...")
    print("    Mientras tanto navegá por los menúes (descargas, contratos, liquidaciones).")
    page.wait_for_timeout(30000)
    page.screenshot(path=str(OUT/"03_explored.png"), full_page=True)

    # Guardar logs
    (OUT/"requests.json").write_text(json.dumps(REQ_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT/"responses.json").write_text(json.dumps(RESP_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT/"token.txt").write_text(TOKEN[0] or "(no capturado)", encoding="utf-8")

    print(f"\n[+] {len(REQ_LOG)} requests / {len(RESP_LOG)} JSON responses logueadas")
    print(f"[+] Token: {'SI' if TOKEN[0] else 'NO'}")
    print(f"[+] Archivos en {OUT}")

    # Listar dominios únicos
    domains = {}
    for r in REQ_LOG:
        host = r["url"].split("/")[2] if "://" in r["url"] else "?"
        domains[host] = domains.get(host, 0) + 1
    print(f"\n[+] Dominios:")
    for d, c in sorted(domains.items(), key=lambda x: -x[1])[:10]:
        print(f"   {c:5d}  {d}")

    # Listar endpoints JSON únicos (path)
    paths = {}
    for r in RESP_LOG:
        path = r["url"].split("?")[0]
        paths[path] = paths.get(path, 0) + 1
    print(f"\n[+] Endpoints JSON 200 OK:")
    for path, c in sorted(paths.items(), key=lambda x: -x[1])[:30]:
        print(f"   {c:3d}  {path[:120]}")

    print("\n[+] Mantengo el browser abierto para que sigas navegando. Cerrá la ventana cuando termines.")
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
