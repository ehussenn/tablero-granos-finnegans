import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    if "entregas" not in page.url:
        page.goto("https://portal.intagro.com/entregas/?area=GV",wait_until="domcontentloaded"); page.wait_for_timeout(4000)
    info=page.evaluate("""()=>{
        const foot=document.querySelector('.dataTables_info'); 
        const sel=document.querySelector('select[name$=_length], select.custom-select, .dataTables_length select');
        const opts=sel?Array.from(sel.options).map(o=>o.value):[];
        // expandir primera fila con '+'
        return {info:foot?foot.innerText:'', lenOpts:opts, lenId:sel?sel.id:null};
    }""")
    print("FOOTER:", info["info"])
    print("LENGTH opts:", info["lenOpts"], "id:", info["lenId"])
    # intentar expandir una fila clickeando el '+'
    try:
        page.locator("td:has-text('+')").first.click(timeout=3000); page.wait_for_timeout(1500)
    except: pass
    det=page.evaluate("""()=>{
        // buscar filas de detalle reveladas
        const tx=document.body.innerText;
        const m=tx.match(/(da.?ad|quebrad|hectol|cuerpo|materia|merma|verde)[^\n]{0,40}/gi);
        return m?m.slice(0,12):[];
    }""")
    print("DETALLE tras expandir:", det)
    b.close()
