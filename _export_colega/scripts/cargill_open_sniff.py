"""Abre Cargill logueado (ventana visible) y GUARDA cada respuesta de la API
mientras el usuario navega. Sirve para que el usuario vaya a la pantalla de
CALIDAD y nosotros capturemos de qué endpoint sale.
Deja la ventana abierta hasta que se cierre."""
import sys, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".cargill_profile"
OUT = ROOT / "scraper" / "out" / "cargill_sniff"
OUT.mkdir(parents=True, exist_ok=True)
# limpiar capturas viejas
for f in OUT.glob("*.json"):
    try: f.unlink()
    except: pass
LOG = OUT / "_log.txt"
LOG.write_text("", encoding="utf-8")
n = [0]
QUAL = ["humed","impure","ardid","avari","partid","verde","calid","quality","grade","analis","merma","descuent","disc","factor","grado"]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950}, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    def on_resp(r):
        try:
            u = r.url
            if "api.cglcloud.com" not in u: return
            if "json" not in r.headers.get("content-type",""): return
            body = r.text()
            n[0]+=1
            low = body.lower()
            score = sum(low.count(q) for q in QUAL)
            tag = "QUAL" if score>3 else "----"
            safe = re.sub(r'[^a-z0-9]+','_', u.split('cglcloud.com')[-1].split('?')[0].lower())[:50]
            (OUT / f"{n[0]:03d}_{tag}_{safe}.json").write_text(body, encoding="utf-8")
            with open(LOG,"a",encoding="utf-8") as f:
                f.write(f"{n[0]:03d} [{tag} score={score}] {r.request.method} {u}\n")
        except Exception: pass
    page.on("response", on_resp)
    try: page.goto("https://www.mycargill.com/cascsa/v2/app/Movements", wait_until="domcontentloaded", timeout=60000)
    except Exception: pass
    print("[+] Ventana de Cargill abierta. Navegá hasta donde ves las CALIDADES.", flush=True)
    print("[+] Voy guardando todo lo que carga. Cuando estés en la pantalla de calidad, avisá.", flush=True)
    try: ctx.wait_for_event("close", timeout=0)
    except Exception: pass
    print("[+] ventana cerrada", flush=True)
