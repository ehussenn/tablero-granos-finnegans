"""ACA: visita cada analizacuenta.asp?xcontrol=N y extrae info de tablas HTML."""
from __future__ import annotations
import sys, os, json, time
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

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto("https://www.acabase.com.ar/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Login si hace falta
    if "pcoop" not in page.url:
        try:
            page.locator("a:has-text('Ingresar')").first.click(timeout=3000)
            page.wait_for_timeout(2000)
            page.locator("#usuario").fill(USER)
            page.locator("input[type='password']").first.fill(PWD)
            page.locator("button:has-text('Ingresar')").first.click()
            page.wait_for_timeout(10000)
        except: pass

    print(f"[+] URL: {page.url}")

    # Explorar xcontrol 1..30
    results = {}
    fecha = time.strftime("%d/%m/%Y")
    for n in [1, 2, 3, 4, 5, 9, 16, 27, 29] + list(range(6, 30)):
        if n in results: continue
        url = f"https://www.acabase.com.ar/ACAbase_Dir/analizacuenta.asp?xcontrol={n}&xfecha={fecha}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            title = ""
            try: title = page.title()
            except: pass
            # Tabs/encabezados
            h1 = ""
            try:
                for sel in ["h1", "h2", "h3", ".titulo", ".encabezado", "td.titulo", "th"]:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        h1 = el.inner_text(timeout=500).strip()[:80]
                        if h1: break
            except: pass
            # Contar tablas y filas
            tables = page.locator("table").all()
            ntbl = len(tables)
            nrows = 0
            for t in tables[:5]:
                try: nrows += t.locator("tr").count()
                except: pass
            # Body text sample
            body = ""
            try: body = page.inner_text("body", timeout=2000)[:300]
            except: pass
            results[n] = {"url": url, "title": title, "h1": h1, "n_tables": ntbl, "n_rows": nrows, "body_sample": body}
            print(f"  xcontrol={n:3d}  {ntbl} tbls / {nrows} rows  | {h1[:50]} | {body[:80].replace(chr(10),' / ')[:80]}")
            page.screenshot(path=str(OUT/f"xc_{n:02d}.png"))
            # Si tiene tablas, guardar HTML
            if ntbl > 0:
                html = page.content()
                (OUT/f"xc_{n:02d}.html").write_text(html, encoding="utf-8")
        except Exception as e:
            print(f"  xcontrol={n:3d}  ERR {str(e)[:80]}")

    (OUT/"xcontrol_map.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] {len(results)} xcontrol explorados")
    ctx.close()
