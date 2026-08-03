import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"intagro_nav"; OUT.mkdir(parents=True,exist_ok=True)
caps=[]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    def on_resp(r):
        try:
            u=r.url
            if any(s in u for s in [".js",".css",".woff",".png",".svg",".ico"]): return
            if r.request.method in ("GET","POST"):
                body=r.text()
                if re.search(r"\b(GR|PH|CE|DA|GQ|Rebaja|Bonific|Analis)\b", body) and len(body)<300000:
                    caps.append((r.request.method, u[:90], len(body)))
                    safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:45]
                    (OUT/f"contrato_{safe}.txt").write_text(body[:200000],encoding="utf-8")
        except: pass
    page.on("response", on_resp)
    if "entregas" not in page.url and "contrato" not in page.url:
        page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded"); page.wait_for_timeout(4000)
    # encontrar el primer link de contrato (Cto) y ver su mecanismo
    link=page.evaluate("""()=>{
        const a=document.querySelector('tbody tr a[onclick], tbody tr td a');
        if(!a) return null;
        return {txt:(a.innerText||'').trim(), href:a.getAttribute('href'), oc:(a.getAttribute('onclick')||'').slice(0,120), dt:a.getAttribute('data-target')||a.getAttribute('data-bs-target')};
    }""")
    print("link contrato:", link)
    # clickearlo
    try:
        page.locator("tbody tr td a").first.click(timeout=5000); page.wait_for_timeout(3500)
        print("clickeado")
    except Exception as e: print("click err:", str(e)[:60])
    # ver si abrió modal y su contenido
    modal=page.evaluate("""()=>{
        const m=document.querySelector('.modal.show, .modal[style*=block], #modalContrato, .modal-content');
        if(!m) return {open:false};
        const tabs=Array.from(m.querySelectorAll('a,button,[role=tab]')).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,12);
        return {open:true, tabs:tabs, txt:(m.innerText||'').replace(/\s+/g,' ').slice(0,250)};
    }""")
    print("modal:", modal)
    print("--- requests con rubros ---")
    for m,u,n in caps[:8]: print(f"  {m} {n}b {u}")
    b.close()
