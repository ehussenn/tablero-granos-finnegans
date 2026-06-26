import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DL=ROOT/"scraper"/"out"/"bunge_dl"; DL.mkdir(parents=True,exist_ok=True)
desde=sys.argv[1] if len(sys.argv)>1 else "01/01/2025"
hasta=sys.argv[2] if len(sys.argv)>2 else "26/06/2026"
prod =sys.argv[3] if len(sys.argv)>3 else "Todos"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    r=page.evaluate("""(o)=>{
        function setSel(id,txt){const s=document.getElementById(id);if(!s)return 'no';
            const op=Array.from(s.options).find(x=>x.text.trim().toLowerCase()===txt.toLowerCase());
            if(!op)return 'opt?'; s.value=op.value; s.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';}
        function setFecha(id,v){const f=document.getElementById(id);if(f){f.value=v;f.dispatchEvent(new Event('change',{bubbles:true}));return true}return false}
        const a=setSel('cph_contenido_ucFiltros_Ddl_Producto',o.prod);
        const d=setFecha('cph_contenido_ucFiltros_ucFechaDesde_txtFecha',o.desde);
        const h=setFecha('cph_contenido_ucFiltros_ucFechaHasta_txtFecha',o.hasta);
        return 'prod='+a+' desde='+d+' hasta='+h;
    }""",{"prod":prod,"desde":desde,"hasta":hasta})
    print("[+] filtros:",r); page.wait_for_timeout(600)
    got=None
    for btn in ["#cph_contenido_ucFichaDescargas_btnDescargar","#cph_contenido_ucFiltros_btn_buscar"]:
        if page.locator(btn).count()==0: continue
        try:
            with page.expect_download(timeout=60000) as di:
                page.click(btn)
            d=di.value; got=str(DL/d.suggested_filename); d.save_as(got)
            print("[+] DESCARGA OK via",btn,"->",got); break
        except Exception as e:
            print(f"[!] {btn}: {str(e)[:70]}"); page.wait_for_timeout(1500)
    if not got:
        print("[!] no hubo download. URL:",page.url)
    b.close()
