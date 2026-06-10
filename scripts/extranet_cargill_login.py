"""Abre un browser Playwright con perfil persistente para CARGILL.
El user se loguea manualmente (CAPTCHA/2FA si hace falta) y la sesión queda guardada.
Una vez logueado, dejá la ventana abierta y avísame para continuar con el scraper."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
PROFILE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

# URLs candidatas — el user navegará a la correcta y se loguea
START_URL = "https://www.cargill.com.ar/"

print(f"[+] Abriendo Cargill — perfil persistente en {PROFILE}", flush=True)
print(f"    Una vez logueado, dejá la ventana abierta y avísame.", flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=False,
        viewport={"width": 1500, "height": 950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
    print(f"[+] Pagina abierta. URL: {page.url}", flush=True)
    print(f"[+] Hacé login en la extranet de Cargill ahora. Cuando estes adentro de tu dashboard,")
    print(f"    dejá la ventana abierta y mandame 'listo' en el chat.")
    print(f"    No cierres esta ventana de PowerShell.")

    # Mantener el script vivo hasta Ctrl+C o que se cierre el browser
    try:
        while True:
            try:
                # ping al browser cada 5s para detectar si se cerró
                page.evaluate("1")
            except Exception:
                print(f"[!] Browser cerrado, saliendo.")
                break
            page.wait_for_timeout(5000)
    except KeyboardInterrupt:
        print(f"\n[+] Saliendo. Perfil guardado en {PROFILE}")
        ctx.close()
