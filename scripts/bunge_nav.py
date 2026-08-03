"""Abre el perfil bunge YA LOGUEADO (cookies persistentes) y explora el portal:
vuelca menús/links y sniffea respuestas JSON con señales de calidad.
Headful para que el usuario vea. Guarda hallazgos en scraper/out/bunge_nav."""
import sys, re, json
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "scraper" / ".bunge_profile"
OUT = ROOT / "scraper" / "out" / "bunge_nav"; OUT.mkdir(parents=True, exist_ok=True)
for f in OUT.glob("*"):
    try: f.unlink()
    except: pass
LOG = OUT / "_log.txt"; LOG.write_text("", encoding="utf-8")
QUAL = ["humed","impure","ardid","avari","partid","verde","calid","quality","grade","grado",
        "analis","merma","descuent","factor","hectol","protei","danad","dañad","quebr","picad","chuzo","ctg"]
n=[0]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir=str(PROFILE), headless=False,
        viewport={"width":1550,"height":950}, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    def on_resp(r):
        try:
            ct=r.headers.get("content-type","")
            u=r.url
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".gif"]): return
            body=r.text()
            if len(body)<5: return
            low=body.lower()
            score=sum(low.count(q) for q in QUAL)
            if "json" not in ct and score<4: return
            n[0]+=1
            tag="QUAL" if score>3 else "----"
            safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:55]
            (OUT/f"{n[0]:03d}_{tag}_{safe}.txt").write_text(body[:200000],encoding="utf-8")
            with open(LOG,"a",encoding="utf-8") as f:
                f.write(f"{n[0]:03d} [{tag} score={score}] {r.request.method} {u}\n")
        except Exception: pass
    page.on("response", on_resp)

    print("URL actual:", page.url, flush=True)
    page.wait_for_timeout(2500)
    links = page.evaluate("""() => Array.from(document.querySelectorAll('a, [onclick], .menu-item, li'))
        .map(e=>({t:(e.innerText||'').trim().slice(0,40), href:e.getAttribute('href')||'', oc:(e.getAttribute('onclick')||'').slice(0,60)}))
        .filter(x=>x.t && (x.href||x.oc))""")
    seen=set(); uniq=[]
    for l in links:
        k=l["t"]+l["href"]
        if k in seen: continue
        seen.add(k); uniq.append(l)
    (OUT/"_links.json").write_text(json.dumps(uniq,ensure_ascii=False,indent=1),encoding="utf-8")
    print(f"[+] {len(uniq)} links/acciones volcados a _links.json", flush=True)
    for l in uniq[:70]:
        print("  ", l["t"], "|", l["href"][:50], "|", l["oc"][:40], flush=True)

    print("\n[+] Ventana abierta y logueada. Sniffeando. NO cerrar.", flush=True)
    print("[+] Guardo en", OUT, flush=True)
    try: ctx.wait_for_event("close", timeout=0)
    except Exception: pass
    print("[+] cerrada", flush=True)
