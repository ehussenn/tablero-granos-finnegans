"""Cargill GPS v2: navega a Descargas, Documentos, Cobros y Pagos, Avances.
Cierra el popup primero. Saca screenshots y captura las URLs del menú izquierdo."""
import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.mycargill.com/cascsa/v2/app/Dashboard", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    # Cerrar popup si aparece
    for sel in ['button:has-text("X")','button[aria-label="Cerrar"]','.modal button.close','[class*="close"]','svg[class*="close"]']:
        try:
            b = page.locator(sel).first
            if b.count() and b.is_visible():
                b.click(timeout=2000); print(f"[+] Cerró popup ({sel})", flush=True); page.wait_for_timeout(1500); break
        except: pass

    page.screenshot(path=str(OUT/"cargill_v2_dashboard.png"), full_page=True)
    print(f"[+] Dashboard: {page.url}")

    # Extraer links de TODO el sidebar izquierdo (todos los a y button con href/data-url)
    print(f"\n[+] LINKS del menu izquierdo:", flush=True)
    sidebar_html = page.locator("nav, aside, [class*='sidebar'], [class*='menu']").first.inner_html(timeout=4000) if page.locator("nav").count() else page.content()
    (OUT/"cargill_v2_sidebar.html").write_text(sidebar_html[:50000], encoding="utf-8")

    # Listar todos los links visibles con texto
    all_links = page.locator("a, button[onclick], [role='button']").all()
    seen = set()
    for a in all_links[:100]:
        try:
            text = (a.inner_text(timeout=300) or "").strip()
            href = a.get_attribute("href", timeout=300)
            if href and text and href not in seen:
                seen.add(href)
                # Filtrar a los que parecen ser nav
                if any(kw in text.lower() for kw in ("contrato","descarga","document","cobro","pago","cuenta","avance","despacho","liquid","carta","entrega")):
                    print(f"    • {text:<35} -> {href}", flush=True)
        except: pass

    # Probar URLs directas v2
    print(f"\n[+] Probando URLs v2 candidatas:", flush=True)
    candidates = [
        ("descargas",       "https://www.mycargill.com/cascsa/v2/app/Movements"),
        ("descargas_pages", "https://www.mycargill.com/cascsa/v2/app/Descargas"),
        ("contratos",       "https://www.mycargill.com/cascsa/v2/app/Contracts"),
        ("documentos",      "https://www.mycargill.com/cascsa/v2/app/Documents"),
        ("cobros_pagos",    "https://www.mycargill.com/cascsa/v2/app/CobrosYPagos"),
        ("cuenta_corriente","https://www.mycargill.com/cascsa/v2/app/CuentaCorriente"),
        ("avances",         "https://www.mycargill.com/cascsa/v2/app/Avances"),
        ("ordenes_despacho","https://www.mycargill.com/cascsa/v2/app/OrdenesDespacho"),
    ]
    for name, url in candidates:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4500)
            final_url = page.url
            title = page.title()
            page.screenshot(path=str(OUT/f"cargill_v2_{name}.png"), full_page=True)
            # Cuanto contenido tabular hay?
            ntr = page.locator("tr, [role='row']").count()
            ncards = page.locator("[class*='card'], [class*='Card']").count()
            redirected = "Dashboard" in final_url
            tag = "↪ REDIR" if redirected else "✓"
            print(f"  {tag} {name:<20} → {final_url[-60:]} | title='{title}' | rows={ntr} cards={ncards}", flush=True)
        except Exception as e:
            print(f"  ✗ {name:<20} ERR: {str(e)[:80]}", flush=True)

    print(f"\n[+] Done. Screenshots en {OUT}", flush=True)
    try:
        while True:
            try: page.evaluate("1")
            except: break
            page.wait_for_timeout(5000)
    except: pass
    ctx.close()
