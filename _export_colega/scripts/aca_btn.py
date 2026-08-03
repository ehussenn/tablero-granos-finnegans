import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    info=page.evaluate("""()=>{
        const ing=document.getElementById('ingresar');
        const forms=Array.from(document.querySelectorAll('form')).map(f=>({action:f.action,method:f.method,id:f.id,name:f.name}));
        const u=document.getElementById('usuario');
        return {ingHTML:ing?ing.outerHTML.slice(0,200):'no', ingOnclick:ing?ing.getAttribute('onclick'):null,
            forms:forms, usuarioForm: u&&u.form?{action:u.form.action,method:u.form.method}:null,
            usuarioVisible: u?u.offsetParent!==null:null};
    }""")
    import json; print(json.dumps(info,ensure_ascii=False,indent=1)[:900])
    b.close()
