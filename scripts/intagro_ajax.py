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
            if any(s in u for s in [".css",".woff",".png",".svg",".ico"]): return
            body=r.text()
            if re.search(r"Rebaja|Bonific|\bPH\b|\bGQ\b|obtener_contrato|Analis", body):
                caps.append((r.request.method, u[:95], len(body), r.request.post_data))
                safe=re.sub(r"[^a-z0-9]+","_",re.sub(r"https?://","",u).split("?")[0].lower())[:45]
                (OUT/f"ajax_{len(caps)}_{safe}.txt").write_text(body[:200000],encoding="utf-8")
        except: pass
    page.on("response", on_resp)
    if "entregas" not in page.url:
        page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded"); page.wait_for_timeout(4000)
    # llamar la función directamente
    r=page.evaluate("""()=>{ try{ if(typeof obtener_contrato==='function'){obtener_contrato(2503,69753611,'GV'); return 'llamada';} return 'no func';}catch(e){return 'err '+e.message;} }""")
    print("obtener_contrato:", r)
    page.wait_for_timeout(5000)
    # leer modal -> solapa Analisis
    page.evaluate("""()=>{const t=Array.from(document.querySelectorAll('a,button,[role=tab]')).find(e=>/an[aá]lisis/i.test((e.innerText||'').trim())); if(t)t.click();}""")
    page.wait_for_timeout(1500)
    modal=page.evaluate("""()=>{const m=document.querySelector('.modal.show,.modal-content,[id*=ontrato]'); if(!m)return 'no modal';
        const rows=Array.from(m.querySelectorAll('table tr')).map(tr=>Array.from(tr.querySelectorAll('th,td')).map(c=>c.innerText.trim())).filter(r=>r.length);
        return rows.slice(0,10);}""")
    print("modal analisis filas:"); 
    import json; print(json.dumps(modal,ensure_ascii=False)[:700])
    print("--- requests capturadas ---")
    for m,u,n,pd in caps[:8]: print(f"  {m} {n}b {u} | body={str(pd)[:60]}")
    b.close()
