import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    rows=page.evaluate("""()=>{const t=document.getElementById('cph_contenido_ucFichaDescargas_GrillaFormato_grilla');
        return t?Array.from(t.querySelectorAll('tr')).map(tr=>Array.from(tr.cells).map(c=>c.innerText.trim())):[]}""")
    for r in rows: print(r)
    print("--- botones/links con descarga/generar ---")
    btns=page.evaluate("""()=>Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit]'))
        .map(e=>({t:(e.innerText||e.value||'').trim(), id:e.id, href:(e.getAttribute('href')||'').slice(0,50)}))
        .filter(x=>/descarg|generar|export|archivo|txt|excel|buscar/i.test(x.t)||/btn|download/i.test(x.id))""")
    for x in btns: print(x)
    b.close()
