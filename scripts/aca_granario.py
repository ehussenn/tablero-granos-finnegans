import sys, re
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9335")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    fr=next((f for f in page.frames if "Totales2" in f.url), None)
    if not fr: print("no frame Totales2"); 
    else:
        rows=fr.evaluate("""()=>{
            const out=[];
            document.querySelectorAll('tr').forEach(tr=>{
                const cells=Array.from(tr.querySelectorAll('td')).map(c=>c.innerText.trim());
                const links=Array.from(tr.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim().slice(0,20),h:(a.getAttribute('href')||'').slice(0,55),oc:(a.getAttribute('onclick')||'').slice(0,55)}));
                if(cells.length) out.push({cells:cells.slice(0,9), links:links.filter(x=>x.h||x.oc)});
            });
            return out;
        }""")
        for r in rows:
            if any(r["cells"]): print("ROW:", r["cells"])
            for l in r["links"]: 
                if l["h"] or l["oc"]: print("    ->",l["t"],"|",l["h"],"|",l["oc"])
    b.close()
