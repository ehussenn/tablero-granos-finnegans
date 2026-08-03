import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    if "entregas" not in page.url:
        page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded"); page.wait_for_timeout(4000)
    # subir page length a 1000
    page.evaluate("""()=>{const s=document.querySelector('.dataTables_length select')||document.querySelector('select');
        if(s){const set=Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'value').set; set.call(s,'1000'); s.dispatchEvent(new Event('change',{bubbles:true}));}}""")
    page.wait_for_timeout(3500)
    nrows=page.evaluate("()=>document.querySelectorAll('tbody tr').length")
    print("filas tras length=1000:", nrows)
    # inspeccionar el ultimo td de la primera fila (el '+')
    info=page.evaluate("""()=>{
        const tr=document.querySelector('tbody tr'); if(!tr) return {};
        const last=tr.lastElementChild;
        return {lastHTML:(last?last.innerHTML:'').slice(0,200), lastTxt:(last?last.innerText:'').trim()};
    }""")
    print("ultimo td:", info)
    # click en el '+' de la primera fila y ver detalle
    page.evaluate("""()=>{const tr=document.querySelector('tbody tr'); if(tr){const t=tr.lastElementChild; (t.querySelector('a,button,i,span')||t).click();}}""")
    page.wait_for_timeout(2000)
    det=page.evaluate("""()=>{const trs=document.querySelectorAll('tbody tr'); 
        // la fila de detalle suele insertarse justo despues
        let out=[];
        for(let i=0;i<Math.min(4,trs.length);i++){out.push(trs[i].innerText.replace(/\s+/g,' ').slice(0,180));}
        return out;}""")
    for d in det: print("  fila:", d)
    b.close()
