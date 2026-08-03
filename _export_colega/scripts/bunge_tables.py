import sys, json
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9333")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    print("URL:", page.url)
    t=page.evaluate("""()=>Array.from(document.querySelectorAll('table')).map(t=>({
        id:t.id, rows:t.querySelectorAll('tr').length,
        head:Array.from((t.querySelector('tr')||{cells:[]}).cells||[]).map(c=>c.innerText.trim()).slice(0,15)
    }))""")
    for x in t: print(x["rows"], x["id"][:60], "|", x["head"][:12])
    # tambien dropdowns de fecha hasta y campaña
    msg=page.evaluate("""()=>{const m=document.querySelector('.alert,.mensaje,#cph_contenido_lblMensaje,[id*=Mensaje],[id*=mensaje]');return m?m.innerText.trim().slice(0,120):'';}""")
    print("MSG:", msg)
    b.close()
