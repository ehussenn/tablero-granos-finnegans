"""Scraper de Finnegans con Playwright — perfil persistente.

Primera corrida: abre Chromium NO-HEADLESS apuntando a Finnegans con un perfil propio.
Hacés login a mano CON CALMA, navegás a donde quieras, y cuando estás logueado
CERRÁS la ventana de Chromium (el botón X). El perfil queda guardado en
scripts/scraper/.profile/

Corridas siguientes: reutiliza el perfil → va directo a la URL pedida.

Uso:
    py scripts/scraper/scrape_view.py 50249             # primera vez (headed)
    py scripts/scraper/scrape_view.py 50249 --headless  # con perfil ya guardado
    py scripts/scraper/scrape_view.py --login           # solo abrir para hacer login
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / ".profile"
OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)


def profile_has_data() -> bool:
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def interactive_login() -> None:
    """Abre Chromium con perfil persistente para que el usuario haga login a mano."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 60)
    print("LOGIN MANUAL — abriendo Chromium con perfil persistente.")
    print("=" * 60)
    print("  1) Hace login en Finnegans con tu usuario y password.")
    print("  2) Una vez adentro del dashboard, CERRA la ventana (X).")
    print("  3) El perfil queda guardado en:")
    print(f"     {PROFILE_DIR}")
    print("  4) Las proximas corridas no van a pedir login.")
    print("=" * 60 + "\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://go.finneg.com/", timeout=45000)
        except Exception as e:
            print(f"  [!] navegando: {e}")

        # esperar a que el usuario cierre la ventana
        try:
            # bloquea hasta que se cierre el context
            context.wait_for_event("close", timeout=0)
        except Exception:
            # se cerro la ventana
            pass
        print("\n[+] Ventana cerrada. Perfil guardado.")


def find_in_all_frames(page, locator_fn):
    """Busca un elemento en la pagina principal y todos sus iframes."""
    # main page
    try:
        el = locator_fn(page)
        if el and el.count() > 0:
            return el
    except Exception:
        pass
    # frames
    for fr in page.frames:
        try:
            el = locator_fn(fr)
            if el and el.count() > 0:
                return el
        except Exception:
            continue
    return None


def try_click_aceptar(page) -> bool:
    """Busca y clickea el boton 'Aceptar' del formulario de parametros."""
    selectors = [
        'button:has-text("Aceptar")',
        'input[type=submit][value="Aceptar"]',
        'input[type=button][value="Aceptar"]',
        'a:has-text("Aceptar")',
        '[role=button]:has-text("Aceptar")',
    ]
    for sel in selectors:
        el = find_in_all_frames(page, lambda ctx, s=sel: ctx.locator(s).first)
        if el and el.count() > 0:
            try:
                el.scroll_into_view_if_needed()
                el.click()
                print(f"  [+] click Aceptar via {sel}")
                return True
            except Exception as e:
                print(f"  [.] fallo click en {sel}: {e}")
    return False


def extract_table_from_frames(page) -> list[list[str]] | None:
    """Recorre frames buscando una tabla con muchas filas (la grilla de resultados)."""
    best = None
    best_rows = 0
    for fr in [page] + list(page.frames):
        try:
            tables = fr.locator("table").all()
        except Exception:
            continue
        for t in tables:
            try:
                rows = t.locator("tr").all()
                if len(rows) > best_rows:
                    best_rows = len(rows)
                    best = (fr, t, rows)
            except Exception:
                continue
    if not best or best_rows < 2:
        return None
    fr, t, rows = best
    print(f"  [+] tabla con {best_rows} filas detectada (frame: {fr.url[:80] if hasattr(fr,'url') else 'main'})")
    out = []
    for r in rows[:500]:  # limite seguro
        try:
            cells = [c.inner_text().strip() for c in r.locator("th,td").all()]
            if cells:
                out.append(cells)
        except Exception:
            continue
    return out


def scrape(view_id: int, headless: bool) -> None:
    if not profile_has_data():
        print("[!] No hay perfil guardado. Corré primero:  py scrape_view.py --login")
        sys.exit(2)

    url = f"https://go.finneg.com/mas/vista?viewID={view_id}"
    print(f"\n[+] Cargando {url}  (headless={headless})", flush=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1600, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # si la sesion vencio, vamos a parar en /login — avisamos
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass
        page.wait_for_timeout(3000)

        cur_url = page.url
        print(f"  [.] URL actual: {cur_url}")
        if "login" in cur_url.lower():
            print("\n[!] La sesion vencio. Corré:  py scrape_view.py --login   para renovarla.")
            context.close()
            return

        ts = time.strftime("%Y%m%d_%H%M%S")

        # Si la vista es parametrizada, apretamos Aceptar y esperamos resultados
        print("\n[+] Buscando boton Aceptar (vista parametrizada)...")
        clicked = try_click_aceptar(page)
        if clicked:
            print("  [.] esperando carga de resultados...")
            try:
                page.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeout:
                pass
            page.wait_for_timeout(5000)

        # Screenshot post-Aceptar
        png = OUT_DIR / f"view_{view_id}_{ts}_results.png"
        htm = OUT_DIR / f"view_{view_id}_{ts}_results.html"
        page.screenshot(path=str(png), full_page=True)
        htm.write_text(page.content(), encoding="utf-8")
        print(f"\n[+] Screenshot:  {png}")
        print(f"[+] HTML:        {htm}")

        # inspeccion rapida
        print(f"\n[+] frames encontrados: {len(page.frames)}")
        for i, f in enumerate(page.frames):
            print(f"    {i}: {f.url[:100]}")

        # extraer tabla
        table = extract_table_from_frames(page)
        if table:
            csv_path = OUT_DIR / f"view_{view_id}_{ts}_data.csv"
            import csv
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerows(table)
            print(f"\n[+] CSV exportado: {csv_path}  ({len(table)} filas incluido encabezado)")
            # preview primeras 5 filas
            print("\n  Preview:")
            for r in table[:5]:
                print(f"    | {' | '.join(c[:25] for c in r[:10])}")
            print(f"\n[+] TOTAL FILAS EN LA VISTA (sin contar header): {max(0,len(table)-1)}")
        else:
            print("\n[!] No detecte tabla con datos. Mira el screenshot.")

        try:
            print(f"\n[+] page title: {page.title()}")
        except Exception:
            pass

        context.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("view_id", nargs="?", type=int, help="viewID a scrapear")
    ap.add_argument("--login", action="store_true", help="solo abrir browser para login manual")
    ap.add_argument("--headless", action="store_true", help="correr sin ventana")
    args = ap.parse_args()

    if args.login:
        interactive_login()
        sys.exit(0)
    if not args.view_id:
        ap.print_help()
        sys.exit(2)

    scrape(args.view_id, headless=args.headless)
