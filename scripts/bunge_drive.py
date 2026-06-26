"""Se conecta por CDP (:9333) a la sesión bunge abierta, SIN cerrarla.
Sniffea respuestas con señales de calidad y vuelca links/menús de la página activa.
Uso: py scripts/bunge_drive.py [explore|click "<texto>"|goto "<url>"|dom]"""
import sys, re, json
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "scraper" / "out" / "bunge_nav"; OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "_log.txt"
QUAL = ["humed","impure","ardid","avari","partid","verde","calid","quality","grade","grado",
        "analis","merma","descuent","factor","hectol","protei","danad","dañad","quebr","picad","chuzo","ctg"]
cmd = sys.argv[1] if len(sys.argv) > 1 else "explore"
arg = sys.argv[2] if len(sys.argv) > 2 else ""
n=[0]

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9333")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    # tomar la pestaña activa visible (la última con foco suele ser [-1])
    if len(ctx.pages) > 1: page = ctx.pages[-1]

    def on_resp(r):
        try:
            u=r.url; ct=r.headers.get("content-type","")
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".gif"]): return
            body=r.text()
            if len(body)<5: return
            low=body.lower(); score=sum(low.count(q) for q in QUAL)
            if "json" not in ct and score<4: return
            n[0]+=1; tag="QUAL" if score>3 else "----"
            safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:55]
            (OUT/f"{n[0]:03d}_{tag}_{safe}.txt").write_text(body[:300000],encoding="utf-8")
            with open(LOG,"a",encoding="utf-8") as f:
                f.write(f"{n[0]:03d} [{tag} score={score}] {r.request.method} {u}\n")
        except Exception: pass
    page.on("response", on_resp)

    print("URL activa:", page.url, flush=True)
    if cmd == "goto" and arg:
        page.goto(arg, wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(3000)
    elif cmd == "click" and arg:
        try:
            page.get_by_text(arg, exact=False).first.click(timeout=6000); page.wait_for_timeout(3500)
            print(f"[+] click '{arg}' OK -> {page.url}", flush=True)
        except Exception as e:
            print(f"[!] click '{arg}': {str(e)[:80]}", flush=True)

    # volcar links/menús/botones de la página activa
    items = page.evaluate("""() => Array.from(document.querySelectorAll('a,[onclick],button,input[type=button],input[type=submit],.menu-item,td'))
        .map(e=>({t:(e.innerText||e.value||'').trim().slice(0,45), href:e.getAttribute('href')||'', oc:(e.getAttribute('onclick')||'').slice(0,70)}))
        .filter(x=>x.t)""")
    seen=set(); uniq=[]
    for l in items:
        k=l["t"]+l["href"]+l["oc"]
        if k in seen or not (l["href"] or l["oc"] or len(l["t"])>1): continue
        seen.add(k); uniq.append(l)
    (OUT/"_links.json").write_text(json.dumps(uniq,ensure_ascii=False,indent=1),encoding="utf-8")
    print(f"[+] {len(uniq)} elementos clickeables:", flush=True)
    for l in uniq[:80]:
        extra = (" href="+l["href"][:40]) if l["href"] else ((" oc="+l["oc"][:40]) if l["oc"] else "")
        print("   •", l["t"], extra, flush=True)
    # NO cerrar el browser (solo desconectar)
    browser.close()  # close() sobre conexión CDP solo desconecta, no mata Chrome
print("[+] desconectado (Chrome sigue abierto)", flush=True)
