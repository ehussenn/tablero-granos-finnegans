"""Cargill GPS: ir a Movements, cerrar popup, clickear 'Exportar Lista' y capturar el archivo descargado."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
DOWNLOADS = ROOT / "scraper" / "downloads" / "cargill"
DOWNLOADS.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    print(f"[+] Movements: {page.url}")

    # Cerrar popup si lo hay (varios selectores)
    for sel in ['button[aria-label*="close" i]','button[aria-label*="Cerrar" i]','.modal-close',
                'button:has-text("X")','svg[class*="close"]','[class*="closeIcon"]',
                'button:has(svg)','div[role="dialog"] button']:
        try:
            els = page.locator(sel).all()
            for el in els:
                if el.is_visible(timeout=500):
                    el.click(timeout=2000)
                    page.wait_for_timeout(800)
                    body = page.locator("body").inner_text(timeout=2000)
                    if "Documentos Cargill" not in body or "Nuevo módulo" not in body:
                        print(f"    [+] Popup cerrado con {sel}"); break
        except: continue

    page.wait_for_timeout(2000)
    page.screenshot(path=str(OUT/"cargill_v2_descargas_v2.png"), full_page=True)

    # Buscar el botón "Exportar Lista"
    print(f"\n[+] Buscando botón Exportar Lista...")
    btn = None
    for sel in ['button:has-text("Exportar Lista")', 'button:has-text("Exportar")',
                'a:has-text("Exportar Lista")', 'a:has-text("Exportar")',
                '[class*="export" i]:not(:has-text("Importar"))']:
        try:
            b = page.locator(sel).last  # last porque está al final de la página
            if b.count() and b.is_visible():
                btn = b
                print(f"    [+] Encontrado con {sel}")
                break
        except: pass

    if btn:
        # Esperamos el download
        try:
            with page.expect_download(timeout=30000) as dl_info:
                btn.click()
                print(f"    [+] Click. Esperando download...")
            download = dl_info.value
            path = DOWNLOADS / download.suggested_filename
            download.save_as(str(path))
            print(f"    [✓] Descargado: {path}  ({path.stat().st_size} bytes)")
        except Exception as e:
            print(f"    [!] No vino download — quizás abre en otra ventana: {e}")
            # Intentar dumpear todos los downloads del context
            print(f"    Probando si hay popup interno o nueva pestaña...")
            page.wait_for_timeout(5000)
            for pg in ctx.pages:
                print(f"      - {pg.url}")
    else:
        print(f"    [!] No encontré botón Exportar")
        # Plot todos los botones visibles para debug
        print(f"    Botones visibles:")
        for b in page.locator("button, a").all()[:50]:
            try:
                t = b.inner_text(timeout=200).strip()
                if t and len(t) < 30: print(f"      • {t!r}")
            except: pass

    print(f"\n[+] Done. Browser abierto.")
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
