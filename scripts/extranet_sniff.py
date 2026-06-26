"""Sniffer genérico de extranets: abre un portal logueado (perfil persistente),
guarda TODA respuesta JSON mientras el usuario navega, y marca las que tienen
señales de CALIDAD. Reutilizable para cualquier cerealera.

Uso:  py scripts/extranet_sniff.py <profile> <url>
  ej: py scripts/extranet_sniff.py .ldc_profile https://mildc.com/webportal
"""
import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
prof = sys.argv[1] if len(sys.argv) > 1 else ".ldc_profile"
url  = sys.argv[2] if len(sys.argv) > 2 else "https://mildc.com/webportal"
PROFILE = ROOT / "scraper" / prof
OUT = ROOT / "scraper" / "out" / (prof.strip(".") + "_sniff")
OUT.mkdir(parents=True, exist_ok=True)
for f in OUT.glob("*"):
    try: f.unlink()
    except: pass
LOG = OUT / "_log.txt"; LOG.write_text("", encoding="utf-8")
QUAL = ["humed","impure","ardid","avari","partid","verde","calid","quality","grade","grado",
        "analis","merma","descuent","factor","hectol","protei","danad","dañad","quebr","picad","chuzo"]
n = [0]
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1500,"height":950}, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    def on_resp(r):
        try:
            ct = r.headers.get("content-type","")
            if "json" not in ct: return
            u = r.url
            if any(s in u for s in [".js",".css",".woff",".png",".svg"]): return
            body = r.text()
            if len(body) < 5: return
            n[0]+=1
            score = sum(body.lower().count(q) for q in QUAL)
            tag = "QUAL" if score>3 else "----"
            safe = re.sub(r"[^a-z0-9]+","_", re.sub(r"https?://","",u).split("?")[0].lower())[:55]
            (OUT / f"{n[0]:03d}_{tag}_{safe}.json").write_text(body, encoding="utf-8")
            with open(LOG,"a",encoding="utf-8") as f:
                f.write(f"{n[0]:03d} [{tag} score={score}] {r.request.method} {u}\n")
        except Exception: pass
    page.on("response", on_resp)
    try: page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e: print("goto:", str(e)[:60])
    print(f"[+] Abierto {url} (perfil {prof}). Navegá a la CALIDAD/análisis.", flush=True)
    print("[+] Guardo todo en", OUT, flush=True)
    print("[+] Cuando estés viendo la calidad, avisá. Cerrá la ventana al terminar.", flush=True)
    try: ctx.wait_for_event("close", timeout=0)
    except Exception: pass
    print("[+] cerrada", flush=True)
