import sys, json
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"bunge_nav"; OUT.mkdir(parents=True,exist_ok=True)
DL=ROOT/"scraper"/"out"/"bunge_dl"; DL.mkdir(parents=True,exist_ok=True)
desde = sys.argv[1] if len(sys.argv)>1 else "01/01/2025"
prod  = sys.argv[2] if len(sys.argv)>2 else "Todos"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    # set producto + fecha desde via evaluate
    r=page.evaluate("""(o)=>{
        function setSel(id,txt){const s=document.getElementById(id);if(!s)return 'no '+id;
            const op=Array.from(s.options).find(x=>x.text.trim().toLowerCase()===txt.toLowerCase());
            if(!op)return 'opt? '+txt; s.value=op.value; s.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';}
        const a=setSel('cph_contenido_ucFiltros_Ddl_Producto', o.prod);
        const fd=document.getElementById('cph_contenido_ucFiltros_ucFechaDesde_txtFecha');
        if(fd){fd.value=o.desde; fd.dispatchEvent(new Event('change',{bubbles:true}));}
        return 'prod='+a+' fechaSet='+(!!fd);
    }""", {"prod":prod,"desde":desde})
    print("[+] filtros:", r)
    page.wait_for_timeout(800)
    # click Buscar y esperar postback o download
    dl_path=None
    try:
        with page.expect_download(timeout=8000) as di:
            page.click("#cph_contenido_ucFiltros_btn_buscar")
        d=di.value; dl_path=str(DL/d.suggested_filename); d.save_as(dl_path)
        print("[+] DESCARGA:", dl_path)
    except Exception:
        page.wait_for_timeout(4000)
        print("[+] sin download directo; postback. URL:", page.url)
    # volcar la tabla de resultados (filas)
    rows=page.evaluate("""()=>{
        const t=document.querySelector('table');
        if(!t) return {n:0,head:[],sample:[]};
        const rs=Array.from(t.querySelectorAll('tr')).map(tr=>Array.from(tr.querySelectorAll('th,td')).map(c=>c.innerText.trim()));
        return {n:rs.length, head:rs[0]||[], sample:rs.slice(1,4)};
    }""")
    print("[+] tabla filas:", rows["n"]); print("   head:", rows["head"][:20]); 
    for s in rows["sample"]: print("   row:", s[:20])
    # listar botones de exportar
    exps=page.evaluate("""()=>Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit]'))
        .map(e=>(e.innerText||e.value||'').trim()).filter(t=>/excel|export|descarg|csv|xls/i.test(t))""")
    print("[+] export btns:", exps)
    (OUT/"_search_state.html").write_text(page.content()[:500000],encoding="utf-8")
    b.close()
