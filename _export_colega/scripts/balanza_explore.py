"""Login + exploración de balanza.agronasaja.com (la app de balanza/análisis).

Loguea con las credenciales BALANZA_USER/BALANZA_PASS del .env, guarda perfil persistente, y vuelca:
- screenshot post-login
- HTML
- listado de links del menú, botones e inputs (para mapear dónde están
  los contratos y el análisis)
"""
from __future__ import annotations
import sys, time, json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from _env import need

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".balanza_profile"
OUT = ROOT / "scraper" / "out"
PROFILE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

USER = need("BALANZA_USER")
PASS = need("BALANZA_PASS")
BASE = "https://balanza.agronasaja.com"

def run(headless=True):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width":1500,"height":950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # sniff de respuestas de API (login/auth)
        API_HITS=[]
        def on_resp(r):
            try:
                u=r.url
                if 'api.agronasaja.com/api/' in u:
                    body=''
                    try:
                        if 'json' in (r.headers.get('content-type','')): body=r.text()[:400]
                    except Exception: pass
                    line=f"[NET {r.status}] {r.request.method} {u}"
                    API_HITS.append(line+"  "+body)
                    print("    "+line[:120])
            except Exception: pass
        page.on("response", on_resp)
        print(f"[+] goto {BASE}/login")
        page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=60000)
        try: page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout: pass
        page.wait_for_timeout(2500)

        # ¿ya logueado? (si el perfil tiene sesión, /login puede redirigir)
        if "login" in page.url.lower():
            print("[+] formulario de login, completando (type con eventos)...")
            u = page.locator('input[name=name]').first
            pw = page.locator('input[name=password]').first
            u.click(); u.fill(""); u.type(USER, delay=60)
            pw.click(); pw.fill(""); pw.type(PASS, delay=60)
            page.wait_for_timeout(400)
            btn = page.locator('button[type=submit]').first
            try:
                print(f"    submit disabled? {btn.is_disabled()}")
            except Exception: pass
            # esperar a que se habilite (Angular valida el form)
            for _ in range(20):
                try:
                    if not btn.is_disabled(): break
                except Exception: break
                page.wait_for_timeout(200)
            btn.click()
            print("    click Iniciar sesión")
            try: page.wait_for_url(lambda u: "login" not in u, timeout=15000)
            except PWTimeout: print("    [.] no salió de /login en 15s")
            page.wait_for_timeout(2500)
            # capturar mensajes de error visibles
            for s in ['.error','.alert','mat-error','.toast','.mensaje','[class*=error i]']:
                for el in page.locator(s).all()[:5]:
                    try:
                        t=(el.inner_text() or '').strip()
                        if t: print(f"    [err {s}] {t[:120]}")
                    except Exception: pass

        print(f"[+] URL post-login: {page.url}")
        # navegar a Liquidaciones Compras para capturar las llamadas API reales
        print("[+] navegando a /liquidaciones-compras-list ...")
        try:
            page.goto(f"{BASE}/liquidaciones-compras-list", wait_until="domcontentloaded", timeout=40000)
            try: page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout: pass
            page.wait_for_timeout(4000)
        except Exception as e:
            print(f"    [!] {e}")
        print(f"[+] URL actual: {page.url}")
        ts = time.strftime("%Y%m%d_%H%M%S")
        png = OUT / f"balanza_{ts}.png"; htm = OUT / f"balanza_{ts}.html"
        page.screenshot(path=str(png), full_page=True)
        htm.write_text(page.content(), encoding="utf-8")
        print(f"[+] screenshot: {png}")
        print(f"[+] html: {htm}")

        # mapear navegación
        def grab(sel):
            out=[]
            for el in page.locator(sel).all()[:60]:
                try:
                    t=(el.inner_text() or "").strip().replace("\n"," ")[:50]
                    href=el.get_attribute("href") or ""
                    if t or href: out.append((t,href))
                except Exception: pass
            return out
        print("\n=== LINKS / NAV ===")
        for t,h in grab("a"):
            if t or h: print(f"  [{t}] {h}")
        print("\n=== BOTONES ===")
        for t,_ in grab("button"):
            if t: print(f"  <{t}>")
        print("\n=== INPUTS ===")
        for el in page.locator("input,select,textarea").all()[:40]:
            try:
                print(f"  {el.evaluate('e=>e.tagName')} name={el.get_attribute('name')} "
                      f"ph={el.get_attribute('placeholder')} fc={el.get_attribute('formcontrolname')}")
            except Exception: pass
        print(f"\n[+] title: {page.title()}")
        hits = OUT / f"balanza_api_{ts}.txt"
        hits.write_text("\n\n".join(API_HITS), encoding="utf-8")
        print(f"\n[+] {len(API_HITS)} llamadas API guardadas en {hits}")
        ctx.close()

if __name__ == "__main__":
    run(headless=("--headed" not in sys.argv) )
