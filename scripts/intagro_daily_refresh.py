"""Refresh Intagro (portal.intagro.com) — login headless con perfil persistente,
mapea el menú y baja la cuenta corriente / liquidaciones a data/intagro/.
Primera corrida: exploratoria (dump de links + screenshots en scripts/scraper/out/intagro/).
"""
from __future__ import annotations
import sys, os, json
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "scripts" / "scraper" / ".intagro_profile"
OUT = ROOT / "scripts" / "scraper" / "out" / "intagro"
OUT.mkdir(parents=True, exist_ok=True)
DATA = ROOT / "data" / "intagro"
DATA.mkdir(parents=True, exist_ok=True)

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
USER = os.environ.get("INTAGRO_USER", "")
PWD = os.environ.get("INTAGRO_PASS", "")

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def main():
    from playwright.sync_api import sync_playwright
    capturas = []
    def on_resp(r):
        try:
            u = r.url
            if any(s in u for s in (".js", ".css", ".woff", ".png", ".svg", ".ico", ".map", "google")): return
            ct = r.headers.get("content-type", "")
            if "json" in ct.lower() and r.status == 200:
                try: body = r.text()
                except Exception: return
                if len(body) > 50:
                    capturas.append({"url": u, "body": body[:200000]})
        except Exception: pass
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless="--visible" not in sys.argv,
                                                   viewport={"width": 1500, "height": 950})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_resp)
        page.goto("https://portal.intagro.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        intentos = [(USER, PWD), (os.environ.get("FINNEGANS_WEB_USER", ""), PWD)]
        for u, pw in intentos:
            if page.locator("#email").count() == 0: break
            if not u: continue
            log(f"login con {u[:3]}*** ...")
            page.fill("#email", u); page.fill("#password", pw)
            page.locator("button:has-text('Ingresar')").first.click(timeout=5000)
            page.wait_for_timeout(7000)
            err = page.evaluate("() => (document.body.innerText||'').includes('incorrectos')")
            if not err: break
            log("  rechazado, pruebo siguiente credencial")
        log(f"post-login: {page.url}")
        page.screenshot(path=str(OUT / "01_home.png"), full_page=True)
        # mapear links del menú
        links = page.evaluate("""() => Array.from(document.querySelectorAll('a')).map(a =>
            ({t: (a.innerText||'').replace(/\\s+/g,' ').trim(), h: a.href}))
            .filter(x => x.t && x.h && x.h.includes('intagro'))""")
        (OUT / "links.json").write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"links: {len(links)}")
        interes = [l for l in links if any(w in l["t"].lower() for w in
                   ("cuenta", "corriente", "saldo", "liquidac", "pago", "entrega", "contrato", "romaneo",
                    "fijac", "factur", "mercader"))]
        for l in interes: log(f"  interes: {l['t'][:38]} -> {l['h'][:70]}")
        # visitar las páginas de interés y capturar JSON + tablas
        resultado = {"actualizado": datetime.now().isoformat()[:16], "paginas": {}}
        vistos = set()
        for i, l in enumerate(interes[:8]):
            if l["h"] in vistos: continue
            vistos.add(l["h"])
            try:
                log(f"abriendo: {l['t'][:40]}")
                page.goto(l["h"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                # Cuenta Corriente: los saldos son tarjetas y los movimientos cargan al
                # clickear "Ver movimientos" — capturo ambas cosas
                extra = {}
                if "saldos" in l["h"]:
                    extra["saldos"] = page.evaluate("""() => (document.body.innerText.match(/\\$\\s*[\\d\\.,]+\\s*\\n?\\s*Saldo[^\\n]*/g) || []).map(s => s.replace(/\\s+/g,' ').trim())""")
                    botones = page.locator("button:has-text('Ver movimientos'), a:has-text('Ver movimientos')")
                    for bi in range(min(2, botones.count())):
                        try:
                            botones.nth(bi).click(timeout=4000)
                            page.wait_for_timeout(5000)
                        except Exception: pass
                page.screenshot(path=str(OUT / f"1{i}_{l['t'][:14].replace(' ','_')}.png"), full_page=True)
                tablas = page.evaluate("""() => Array.from(document.querySelectorAll('table')).map(tb =>
                    Array.from(tb.querySelectorAll('tr')).map(tr =>
                        Array.from(tr.querySelectorAll('td,th')).map(c => (c.innerText||'').replace(/\\s+/g,' ').trim()))
                    .filter(r => r.some(c => c))).filter(t => t.length > 1)""")
                resultado["paginas"][l["t"][:40]] = {"url": l["h"], "tablas": tablas, **extra}
            except Exception as e:
                log(f"  [!] {e}")
        resultado["json_capturados"] = [{"url": c["url"][:100], "muestra": c["body"][:400]} for c in capturas[-25:]]
        (DATA / "cuenta_corriente_raw.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / "capturas_json.json").write_text(json.dumps(capturas[-40:], ensure_ascii=False), encoding="utf-8")
        n = sum(len(v.get("tablas") or []) for v in resultado["paginas"].values())
        log(f"OK -> data/intagro/cuenta_corriente_raw.json · {len(resultado['paginas'])} páginas · {n} tablas · {len(capturas)} JSON")
        ctx.close()
        return 0

if __name__ == "__main__":
    sys.exit(main())
