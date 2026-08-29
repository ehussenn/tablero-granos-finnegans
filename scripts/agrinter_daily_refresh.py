# Agrinter: login (form Usuario/Contraseña) + mapa del menú + tablas de las páginas de interés.
import sys, os, pathlib, json
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")
BASE = pathlib.Path(r"c:\Users\Public\Documents\Granos\tablero-granos-finnegans")
for line in (BASE / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
SCR = pathlib.Path(__file__).resolve().parent
PROFILE = BASE / "scripts" / "scraper" / ".agrinter_profile"
DATAA = BASE / "data" / "agrinter"
DATAA.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("AGRINTER_URL", "http://200.68.125.7/LoginForm")

def tablas_de(page):
    out = []
    for f in page.frames:
        try:
            ts = f.evaluate("""() => Array.from(document.querySelectorAll('table')).map(tb =>
                Array.from(tb.querySelectorAll('tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td,th')).map(c => (c.innerText||'').replace(/\\s+/g,' ').trim()))
                .filter(r => r.some(c => c))).filter(t => t.length > 1)""")
            out.extend(ts)
        except Exception: pass
    return out

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
                                               viewport={"width": 1500, "height": 950})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    if page.locator("input[type='password']").count() > 0:
        print("login ...")
        page.locator("input[type='text']").first.fill(os.environ["AGRINTER_USER"])
        page.locator("input[type='password']").first.fill(os.environ["AGRINTER_PASS"])
        hecho = False
        for b in ["input[type='submit']", "button[type='submit']", "button:has-text('Ingresar')", "input[value*='ngresar']"]:
            if page.locator(b).count() > 0: page.locator(b).first.click(timeout=4000); hecho = True; break
        if not hecho: page.locator("input[type='password']").first.press("Enter")
        page.wait_for_timeout(7000)
    print("post-login:", page.url)
    page.screenshot(path=str(SCR / "agrinter_home.png"), full_page=True)
    links = page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => ({t: (a.innerText||'').replace(/\\s+/g,' ').trim(), h: a.href || ''})).filter(x => x.t)")
    (SCR / "agrinter_links.json").write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8")
    print("links:", len(links))
    for l in links[:30]: print("   ", l["t"][:45], "->", l["h"][:70])
    interes = [l for l in links if any(w in l["t"].lower() for w in
               ("cuenta", "corriente", "saldo", "liquidac", "pago", "entrega", "contrato", "romaneo", "carta", "aplicac", "grano", "cereal"))]
    resultado = {"actualizado": datetime.now().isoformat()[:16], "paginas": {}}
    vistos = set()
    for i, l in enumerate(interes[:10]):
        if not l["h"] or l["h"] in vistos or l["h"].startswith("javascript"): continue
        vistos.add(l["h"])
        try:
            print("abriendo:", l["t"][:40])
            page.goto(l["h"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            page.screenshot(path=str(SCR / f"agrinter_{i}_{l['t'][:12].replace(' ','_').replace('/','-')}.png"), full_page=True)
            resultado["paginas"][l["t"][:40]] = tablas_de(page)
        except Exception as e:
            print("   [!]", str(e)[:70])
    (DATAA / "cuenta_corriente_raw.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for v in resultado["paginas"].values())
    print(f"OK -> data/agrinter/cuenta_corriente_raw.json · {len(resultado['paginas'])} páginas · {n} tablas")
    ctx.close()
