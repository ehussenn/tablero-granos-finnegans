import sys,re,json,time
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
CID=sys.argv[1] if len(sys.argv)>1 else "732287"
reqs=[]; resps=[]
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    page=b.contexts[0].pages[0]
    def on_req(r):
        if 'webreport/data' in r.url or 'standardDF' in r.url:
            reqs.append((r.url, r.post_data))
    def on_resp(r):
        if 'webreport/data' in r.url or 'standardDF' in r.url:
            try: resps.append((r.url, r.text()))
            except: pass
    page.on("request",on_req); page.on("response",on_resp)
    url=f"https://go.finneg.com/mas/vista?fafViewCode=DF_VIEWER&pk={CID}&claseVO=ContratoVentaGranosVO&appitemID=50249"
    try: page.goto(url,wait_until="domcontentloaded",timeout=45000)
    except Exception as e: print("goto:",str(e)[:50])
    for _ in range(20):
        time.sleep(1)
        if sum(1 for u,_ in resps if 'webreport/data' in u)>=4: break
    b.close()
print(f"reqs={len(reqs)} resps={len(resps)}")
# guardar template completo del grid liquidaciones + extraer pks
liq_pks=set()
for i,(u,body) in enumerate(resps):
    tag='LIQ' if ('Liq. Parcial' in body or 'Nro.Comprobante' in body) else ('ENT' if 'Carta Porte' in body else '?')
    print(f"  resp[{i}] {tag} len={len(body)} ...{u[-40:]}")
    if tag=='LIQ':
        m=re.search(r'<data><!\[CDATA\[(.*?)\]\]>',body,re.S)
        if m:
            for row in m.group(1).split(';'):
                f=row.split(',')
                if f and f[0].strip().isdigit(): liq_pks.add(f[0].strip())
# dump request templates
for i,(u,pd) in enumerate(reqs):
    open(f'scripts/scraper/out/finn_sniff/_reqtpl_{i}.txt','w',encoding='utf-8').write(u+"\n\n====BODY====\n"+(pd or ''))
print("liq pks:",len(liq_pks),sorted(liq_pks)[:8])
json.dump(sorted(liq_pks),open(f'scripts/scraper/out/finn_sniff/_liqpks_{CID}.json','w'))
