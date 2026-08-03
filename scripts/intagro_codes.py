import sys, re, json
from pathlib import Path
from collections import defaultdict
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
q=json.loads((ROOT.parent/"data"/"intagro"/"quality.json").read_text(encoding="utf-8"))
contratos=sorted(set(v["contrato"] for v in q.values() if v.get("contrato")))[:18]
ex=defaultdict(set)
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    for c in contratos:
        try:
            html=page.request.post("https://portal.intagro.com/ajax_altocom/VerContratoAmpliado.php",
                form={"productor":"2503","contrato":c,"areanegocio":"GV"},headers={"X-Requested-With":"XMLHttpRequest"}).text()
        except: continue
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S|re.I):
            cells=[re.sub(r"<[^>]+>","",x).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S|re.I)]
            if len(cells)>=7 and re.match(r"^[A-Z]{2}$",cells[3]) and re.match(r"^\d{11}$",cells[2]):
                ex[cells[3]].add(cells[6][:35])
    b.close()
for code in sorted(ex): print(f"  {code}: {list(ex[code])[:3]}")
