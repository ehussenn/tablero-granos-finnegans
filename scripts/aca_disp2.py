import sys
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    page.goto("https://www.acabase.com.ar/consulaco/disponibilidad.asp?xctamadre=185566&xcuenta=18556602&xnombre=AGRONASAJA&xnomzona=SC",wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(3500)
    fr=next((f for f in page.frames if "disponibilidad" in f.url), page.frames[-1])
    # filas con todas sus celdas y links
    rows=fr.evaluate("""()=>{const out=[];
        document.querySelectorAll('tr').forEach(tr=>{
            const cells=Array.from(tr.querySelectorAll('td')).map(td=>{
                const a=td.querySelector('a'); 
                return {t:td.innerText.trim().slice(0,14), h:a?(a.getAttribute('href')||'').slice(0,60):''};
            });
            if(cells.some(c=>c.t)) out.push(cells);
        });
        return out.slice(0,25);
    }""")
    # headers
    heads=fr.evaluate("()=>Array.from(document.querySelectorAll('th')).map(t=>t.innerText.trim()).filter(Boolean)")
    print("HEADS:",heads)
    for r in rows:
        line=" | ".join(c["t"] for c in r)
        hrefs=[c["h"] for c in r if c["h"]]
        if "Soja" in line or "25-26" in line or "Trigo" in line:
            print("ROW:",line)
            for h in hrefs: print("     ->",h)
    b.close()
