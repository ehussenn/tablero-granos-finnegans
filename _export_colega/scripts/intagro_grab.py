"""Sobre la sesión intagro (CDP :9334) ya logueada: captura respuestas JSON con
calidad y baja exports. Vuelca estructura de la página activa."""
import sys, re, json
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DL=ROOT/"scraper"/"out"/"intagro_dl"; DL.mkdir(parents=True,exist_ok=True)
OUT=ROOT/"scraper"/"out"/"intagro_nav"; OUT.mkdir(parents=True,exist_ok=True)
action = sys.argv[1] if len(sys.argv)>1 else "export"   # export | analisis | dump
n=[0]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            u=r.url; ct=r.headers.get("content-type","")
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".ico"]): return
            if "json" not in ct: return
            body=r.text()
            if len(body)<20: return
            n[0]+=1
            safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:50]
            (OUT/f"{n[0]:03d}_{safe}.json").write_text(body[:400000],encoding="utf-8")
        except: pass
    page.on("response", on_resp)
    print("URL:", page.url)
    if action=="export":
        try:
            with page.expect_download(timeout=30000) as di:
                page.locator("text=Exportar").first.click()
            d=di.value; fp=str(DL/d.suggested_filename); d.save_as(fp)
            print("[+] EXPORT:", fp)
        except Exception as e: print("[!] export:", str(e)[:90])
    elif action=="analisis":
        try:
            page.locator("text=Análisis").first.click(timeout=6000); page.wait_for_timeout(4000)
            print("[+] Análisis URL:", page.url)
        except Exception as e: print("[!] analisis:", str(e)[:80])
    # dump form + tabla headers de la pág activa
    info=page.evaluate("""()=>({
        inputs:Array.from(document.querySelectorAll('input,select')).map(e=>({id:e.id,type:e.type,ph:e.placeholder,val:(e.value||'').slice(0,15)})).filter(x=>x.type!=='hidden').slice(0,15),
        thead:Array.from(document.querySelectorAll('th')).map(e=>e.innerText.trim()).slice(0,20),
        btns:Array.from(document.querySelectorAll('a,button')).map(e=>(e.innerText||'').trim()).filter(t=>/export|imprimir|aplicar|análisis|analisis/i.test(t)).slice(0,8)
    })""")
    print("inputs:", info["inputs"])
    print("thead:", info["thead"])
    print("btns:", info["btns"])
    b.close()
