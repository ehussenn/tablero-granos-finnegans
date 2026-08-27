"""Refresh cta cte ACA (acabase.com.ar/consulaco) — headless, guarda data/aca/cuenta_corriente.json.
Flujo: perfil persistente → si pide login: Identificarse + user/pass → marco.asp?xllamap=cuentas_datos.asp
→ sigue los links de cuenta (crea_varsession) → parsea las tablas de todos los frames.
Debug: screenshots y HTML en scripts/scraper/out/aca/.
"""
from __future__ import annotations
import sys, os, json
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".aca_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "aca"
OUT.mkdir(parents=True, exist_ok=True)
DATA = ROOT / "data" / "aca"
DATA.mkdir(parents=True, exist_ok=True)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
USER = os.environ.get("ACA_USER", "")
PWD = os.environ.get("ACA_PASS", "")

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def tablas_de(page):
    """Extrae todas las tablas (headers+rows) de todos los frames con contenido."""
    out = []
    for f in page.frames:
        try:
            t = f.evaluate("""() => Array.from(document.querySelectorAll('table')).map(tb => {
                const rows = Array.from(tb.querySelectorAll('tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td,th')).map(c => (c.innerText||'').replace(/\\s+/g,' ').trim()));
                return rows.filter(r => r.some(c => c));
            }).filter(t => t.length > 1)""")
            for tb in t:
                out.append({"frame": f.url[-60:], "rows": tb})
        except Exception:
            pass
    return out

def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless="--visible" not in sys.argv,
                                                   viewport={"width": 1500, "height": 950})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("abriendo cuentas_datos.asp ...")
        page.goto("https://www.acabase.com.ar/consulaco/marco.asp?xllamap=cuentas_datos.asp",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        # ¿pide login? (frame o pagina con password)
        necesita_login = False
        for f in page.frames:
            try:
                if f.locator("input[type='password']").count() > 0: necesita_login = True
            except Exception: pass
        if necesita_login or "login" in page.url.lower():
            # el consulaco muestra su propio form "Ingrese sus datos / Sesión caducada"
            # (Usuario + Contraseña + botón Ingresar) en la misma página o en un frame
            log("re-login (form 'Ingrese sus datos') ...")
            hecho = False
            for f in page.frames:
                try:
                    if f.locator("input[type='password']").count() == 0: continue
                    f.locator("input[type='text']").first.fill(USER, timeout=4000)
                    f.locator("input[type='password']").first.fill(PWD, timeout=4000)
                    for b in ["input[value='Ingresar']", "button:has-text('Ingresar')", "input[type='submit']", "button[type='submit']"]:
                        if f.locator(b).count() > 0:
                            f.locator(b).first.click(timeout=3000); hecho = True; break
                    if not hecho:
                        f.locator("input[type='password']").first.press("Enter"); hecho = True
                    break
                except Exception as e:
                    log(f"  [!] frame login: {e}")
            page.wait_for_timeout(7000)
            page.screenshot(path=str(OUT / "cta_00_postlogin.png"), full_page=True)
            page.goto("https://www.acabase.com.ar/consulaco/marco.asp?xllamap=cuentas_datos.asp",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        page.screenshot(path=str(OUT / "cta_01_cuentas.png"), full_page=True)
        # links de cuentas (crea_varsession) en los frames
        cuentas = []
        for f in page.frames:
            try:
                ls = f.evaluate("""() => Array.from(document.querySelectorAll('a[href*="crea_varsession"]'))
                    .map(a => ({t: (a.innerText||'').replace(/\\s+/g,' ').trim(), h: a.href}))""")
                cuentas.extend(ls)
            except Exception: pass
        log(f"cuentas encontradas: {len(cuentas)} -> {[c['t'][:30] for c in cuentas[:6]]}")
        resultado = {"actualizado": datetime.now().isoformat()[:16], "cuentas": {}}
        if not cuentas:
            # sin links: guardar lo que haya en pantalla igual (tabla de saldos directa)
            resultado["cuentas"]["(pantalla cuentas)"] = tablas_de(page)
        for i, c in enumerate(cuentas[:8]):
            try:
                log(f"abriendo cuenta: {c['t'][:40]}")
                page.goto(c["h"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4500)
                page.screenshot(path=str(OUT / f"cta_1{i}_mov.png"), full_page=True)
                resultado["cuentas"][c["t"] or f"cuenta {i}"] = tablas_de(page)
                page.goto("https://www.acabase.com.ar/consulaco/marco.asp?xllamap=cuentas_datos.asp",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)
            except Exception as e:
                log(f"  [!] {e}")
        (DATA / "cuenta_corriente.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
        n = sum(len(v) for v in resultado["cuentas"].values())
        log(f"OK -> data/aca/cuenta_corriente.json · {len(resultado['cuentas'])} cuentas · {n} tablas")
        ctx.close()
        return 0

if __name__ == "__main__":
    sys.exit(main())
