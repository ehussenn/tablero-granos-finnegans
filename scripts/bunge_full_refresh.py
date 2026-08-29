# Bunge TODO EN UNA CORRIDA: login (handshake captcha con Claude) + elegir cada cuenta +
# mapear menú + capturar las tablas de las páginas con pinta de cta cte / liquidaciones.
import sys, os, time, pathlib, json
sys.stdout.reconfigure(encoding="utf-8")
BASE = pathlib.Path(r"c:\Users\Public\Documents\Granos\tablero-granos-finnegans")
for line in (BASE / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
SCR = pathlib.Path(__file__).resolve().parent
PROFILE = BASE / "scripts" / "scraper" / ".bunge_profile"
RESP = SCR / "bunge_captcha_resp.txt"
if RESP.exists(): RESP.unlink()
DATAB = BASE / "data" / "bunge"
DATAB.mkdir(parents=True, exist_ok=True)

def tablas_de(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('table')).map(tb =>
        Array.from(tb.querySelectorAll('tr')).map(tr =>
            Array.from(tr.querySelectorAll('td,th')).map(c => (c.innerText||'').replace(/\\s+/g,' ').trim()))
        .filter(r => r.some(c => c))).filter(t => t.length > 1 && t[0].length > 1)""")

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=True,
        viewport={"width": 1550, "height": 950}, args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://operacionesbasa.bunge.ar/operacionesbasa/Login.aspx", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if page.locator("input[type='password']").count() > 0:
        page.locator("input[type='text']").first.fill(os.environ["BUNGE_USER"])
        page.locator("input[type='password']").first.fill(os.environ["BUNGE_PASS"])
        page.wait_for_timeout(800)
        cap = None
        imgs = page.locator("img")
        for i in range(imgs.count()):
            try:
                src = (imgs.nth(i).get_attribute("src") or "").lower()
                bb = imgs.nth(i).bounding_box()
                if "captcha" in src: cap = imgs.nth(i); break
                if bb and 60 < bb["width"] < 420 and 25 < bb["height"] < 160: cap = imgs.nth(i)
            except Exception: pass
        if not cap: print("SIN CAPTCHA IMG"); ctx.close(); sys.exit(1)
        cap.screenshot(path=str(SCR / "bunge_captcha.png"))
        print("CAPTCHA LISTO -> bunge_captcha.png · esperando respuesta ...", flush=True)
        for _ in range(360):
            if RESP.exists() and RESP.read_text(encoding="utf-8").strip(): break
            time.sleep(1)
        else:
            print("TIMEOUT captcha"); ctx.close(); sys.exit(1)
        txt = RESP.read_text(encoding="utf-8").strip()
        print("respuesta:", txt)
        ok = False
        for sel in ["input[placeholder*='imagen' i]", "input[placeholder*='texto' i]"]:
            if page.locator(sel).count() > 0: page.locator(sel).first.fill(txt); ok = True; break
        if not ok:
            cajas = page.locator("input[type='text']")
            cajas.nth(cajas.count() - 1).fill(txt)
        page.wait_for_timeout(400)
        for bsel in ["input[type='submit']", "button[type='submit']", "input[value*='ngresar']", "button:has-text('Ingresar')"]:
            if page.locator(bsel).count() > 0: page.locator(bsel).first.click(timeout=4000); break
        page.wait_for_timeout(9000)
        if page.locator("input[type='password']").count() > 0:
            page.screenshot(path=str(SCR / "bunge_fallo.png"), full_page=True)
            print("LOGIN FALLÓ"); ctx.close(); sys.exit(1)
    print("LOGUEADO:", page.url)
    resultado = {"actualizado": time.strftime("%Y-%m-%dT%H:%M"), "cuentas": {}}
    for cuenta in ("123879", "165914"):
        try:
            page.goto("https://operacionesbasa.bunge.ar/operacionesbasa/Paginas/General/SeleccionaCuenta.aspx",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            # clic por JS: los anchors son __doPostBack y a veces el locator no los toma
            hizo = page.evaluate("""(cta) => {
                const a = Array.from(document.querySelectorAll('a')).find(x => (x.innerText||'').trim().startsWith(cta));
                if(a){ a.click(); return true; } return false;
            }""", cuenta)
            if not hizo:
                print(f"   [!] no encontré el link de {cuenta}")
                page.screenshot(path=str(SCR / f"bunge_sel_{cuenta}.png"), full_page=True)
                continue
            page.wait_for_timeout(9000)
            print(f"== cuenta {cuenta}:", page.url)
            page.screenshot(path=str(SCR / f"bunge_c{cuenta}.png"), full_page=True)
            links = page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => ({t: (a.innerText||'').replace(/\\s+/g,' ').trim(), h: a.getAttribute('href') || ''})).filter(x => x.t)")
            interes = [l for l in links if any(w in l["t"].lower() for w in
                       ("cuenta", "corriente", "saldo", "liquidac", "pago", "romaneo", "entrega", "contrato", "certificado", "aplicac"))]
            print("   links interés:", [l["t"][:35] for l in interes])
            datos = {"links": [l["t"] for l in links], "paginas": {}}
            vistos_l = set()
            for l in interes[:10]:
                if l["t"] in vistos_l: continue
                vistos_l.add(l["t"])
                try:
                    hizo = page.evaluate("""(txt) => {
                        const a = Array.from(document.querySelectorAll('a')).find(x => (x.innerText||'').replace(/\\s+/g,' ').trim() === txt);
                        if(a){ a.click(); return true; } return false;
                    }""", l["t"])
                    if not hizo:
                        print(f"   [!] {l['t'][:30]}: no encontrado en DOM"); continue
                    page.wait_for_timeout(8000)
                    # si aparece el modal de filtro: rango "Comienzo del año" + Buscar
                    try:
                        page.evaluate("""() => { const a = Array.from(document.querySelectorAll('a'))
                            .find(x => (x.innerText||'').includes('Comienzo del a')); if(a) a.click(); }""")
                        page.wait_for_timeout(1500)
                        clicked = page.evaluate("""() => { const b = Array.from(document.querySelectorAll('input[type=submit],button,a'))
                            .find(x => ((x.value||x.innerText||'').trim() === 'Buscar')); if(b){ b.click(); return true; } return false; }""")
                        if clicked: page.wait_for_timeout(9000)
                    except Exception: pass
                    ts = tablas_de(page)
                    datos["paginas"][l["t"][:40]] = ts
                    page.screenshot(path=str(SCR / f"bunge_{cuenta}_{l['t'][:12].replace(' ','_').replace('/','-')}.png"), full_page=True)
                    print(f"   {l['t'][:35]}: {len(ts)} tablas · {page.url[-45:]}")
                except Exception as e:
                    print(f"   [!] {l['t'][:30]}: {str(e)[:60]}")
            resultado["cuentas"][cuenta] = datos
        except Exception as e:
            print(f"[!] cuenta {cuenta}: {str(e)[:80]}")
    (DATAB / "cuenta_corriente_raw.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print("OK -> data/bunge/cuenta_corriente_raw.json")
    ctx.close()
