"""Login a Cargill y exploración del dashboard.
Credenciales via env vars (CARGILL_USER, CARGILL_PASS). NO commitear el .py con valores."""
import sys, os, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

USER = os.environ.get("CARGILL_USER")
PASS = os.environ.get("CARGILL_PASS")
if not (USER and PASS): sys.exit("Falta CARGILL_USER/CARGILL_PASS env vars")

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
PROFILE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

LOGIN_URL = "https://www.mycargill.com/cascsa/es/pages/login"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print(f"[+] Abriendo {LOGIN_URL}", flush=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    page.screenshot(path=str(OUT/"cargill_01_login.png"), full_page=True)
    print(f"    URL final: {page.url}", flush=True)
    print(f"    Title:     {page.title()}", flush=True)

    # Detectar si YA estamos logueados: si la URL NO contiene login/sigin
    already_logged = ("/login" not in page.url) and ("/signin" not in page.url) and ("/auth" not in page.url)
    if already_logged:
        print(f"[+] Ya estaba logueado, salto al dashboard", flush=True)
    else:
        # Detectar campos de login — probaremos selectores comunes
        print(f"[+] Buscando campos de login...", flush=True)
        # Diferentes selectores que probar para usuario
        user_selectors = [
            "input[name='username']", "input[name='user']", "input[name='email']",
            "input[type='email']", "input[id*='user']", "input[id*='email']",
            "input[placeholder*='usuario' i]", "input[placeholder*='email' i]",
            "input[placeholder*='cuenta' i]",
        ]
        pass_selectors = [
            "input[name='password']", "input[name='pass']", "input[type='password']",
            "input[id*='pass']",
        ]
        user_field = None
        for sel in user_selectors:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    user_field = el; print(f"    [+] user field: {sel}", flush=True); break
            except: pass
        pass_field = None
        for sel in pass_selectors:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    pass_field = el; print(f"    [+] pass field: {sel}", flush=True); break
            except: pass
        if user_field and pass_field:
            user_field.fill(USER); time.sleep(0.5)
            pass_field.fill(PASS); time.sleep(0.5)
            # Submit
            for sel in ['button[type="submit"]','button:has-text("Ingresar")','button:has-text("Acceder")',
                        'button:has-text("Iniciar sesión")','input[type="submit"]']:
                try:
                    b = page.locator(sel).first
                    if b.count() and b.is_visible(): b.click(); print(f"    [+] click {sel}", flush=True); break
                except: pass
            else:
                # Si no hay boton, enter en pass
                pass_field.press("Enter")
            print(f"[+] Login enviado, esperando redirect...", flush=True)
            page.wait_for_timeout(8000)
            page.screenshot(path=str(OUT/"cargill_02_after_login.png"), full_page=True)
            print(f"    URL post-login: {page.url}", flush=True)
            print(f"    Title:          {page.title()}", flush=True)
        else:
            print(f"[!] no encontre los campos. Screenshot guardado, revisar.", flush=True)

    # Listar todos los links del menu / dashboard
    page.wait_for_timeout(2000)
    page.screenshot(path=str(OUT/"cargill_03_dashboard.png"), full_page=True)
    print(f"\n[+] LINKS visibles en dashboard:", flush=True)
    links = page.locator("a").all()
    for a in links[:60]:
        try:
            text = (a.inner_text(timeout=500) or "").strip()
            href = a.get_attribute("href", timeout=500) or ""
            if text and (any(kw in text.lower() for kw in ("liquid","descarga","operac","contrato","factur","carta","ctg","entrega","movim")) or
                         any(kw in (href or "").lower() for kw in ("liquid","descarga","operac","contrato","factur"))):
                print(f"    • {text:<40} -> {href}", flush=True)
        except: pass

    print(f"\n[+] Dejé la ventana abierta. Mirá los screenshots en {OUT}", flush=True)
    print(f"    Cuando termines de revisar, cerrá la ventana de Chromium para que continuemos.", flush=True)

    # Keep alive hasta que el user cierre el browser
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
print("[+] Profile guardado para futuras corridas", flush=True)
