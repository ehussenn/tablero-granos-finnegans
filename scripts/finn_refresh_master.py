"""Baja fresco el master grid de contratos de venta (viewID=50249) desde Finnegans GO/BSA
via la sesion CDP 9340. Regenera _all_targets.json con los que tienen entregado pend liq>0."""
import sys,re,json,time
from collections import Counter
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
SN="scripts/scraper/out/finn_sniff"
cap={"url":None,"body":None}
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    page=b.contexts[0].pages[0]
    print("URL actual:",page.url[:80])
    allreq=[]
    def on_req(r):
        if 'webreport/data' in r.url:
            allreq.append((r.url,r.post_data))
            if 'fafViewID=50249' in r.url or 'masterWR=1' in r.url:
                cap["url"]=r.url; cap["body"]=r.post_data
    page.on("request",on_req)
    # navegar desde el menú hacia la vista (fuerza carga fresca del master grid)
    try:
        page.goto("https://go.finneg.com/mas/menu",wait_until="domcontentloaded",timeout=45000); time.sleep(3)
    except Exception as e: print("menu:",str(e)[:50])
    try:
        page.goto("https://go.finneg.com/mas/vista?viewID=50249",wait_until="domcontentloaded",timeout=45000)
    except Exception as e: print("vista:",str(e)[:50])
    for _ in range(30):
        time.sleep(1)
        if cap["url"]: break
    print("total webreport reqs vistos:",len(allreq))
    for u,pd in allreq[:8]:
        print("  ->",u[:110],"| masterWR" if 'masterWR=1' in u else "")
    # login check
    txt=page.evaluate("()=>document.body.innerText.slice(0,120)") or ""
    if 'login' in page.url.lower() or 'contrase' in txt.lower() or 'ingresar' in txt.lower():
        print("[!] PARECE DESLOGUEADO. body:",txt[:100].replace(chr(10)," "))
    if not cap["url"]:
        print("[!] no capturé el master grid request"); b.close(); sys.exit(1)
    print("master req capturado. url len",len(cap["url"]),"body len",len(cap["body"] or ""))
    hdr={"content-type":"application/x-www-form-urlencoded; charset=UTF-8"}
    r=page.request.post(cap["url"],data=cap["body"] or "",headers=hdr,timeout=90000)
    xml=r.text()
    b.close()
open(f"{SN}/003_fresh.xml","w",encoding="utf-8").write(xml)
print("respuesta len:",len(xml))
# parsear
fields=re.findall(r'<field name="([^"]+)"',xml); cols=['__PK__']+fields
i=xml.find('<![CDATA[',xml.find('<data>')); j=xml.find(']]>',i)
rows=[r for r in xml[i+9:j].split(';') if r.strip()]
def g(v,n):
    k=cols.index(n); return v[k] if k<len(v) else ''
def num(x):
    try: return float(str(x).replace(',','') or 0)
    except: return 0.0
def cer(org):
    n=(org or '').upper()
    for k,l in [("CARGILL","Cargill"),("DREYFUS","LDC"),("LDC","LDC"),("BUNGE","Bunge"),
                ("ARGENTRADING","Intagro"),("INTAGRO","Intagro"),("COFCO","COFCO"),
                ("COOPERATIVAS ARGENTINAS","ACA"),("A.C.A","ACA"),("FYO","FYO"),("ALLARIA","Allaria"),
                ("VITERRA","Viterra"),("ADM","ADM"),("MOLINOS","Molinos"),("AGD","AGD"),
                ("ACEITERA GENERAL","AGD"),("CHS","CHS"),("AMAGGI","Amaggi"),("GLENCORE","Glencore")]:
        if k in n: return l
    return org[:24] if org else "(s/org)"
tgt=[]
for r in rows:
    v=r.split(',')
    pend=num(g(v,'CANTIDADENTREGADAPENDIENTELIQUIDAR'))
    if pend<=0.05: continue
    tgt.append({"CONTRATOID":g(v,'CONTRATOID'),"CONTRATO":g(v,'CONTRATO'),
                "org":g(v,'ORGANIZACION'),"cer":cer(g(v,'ORGANIZACION')),
                "prod":g(v,'PRODUCTO'),"cos":g(v,'COSECHA'),"pend":round(pend,2)})
json.dump(tgt,open(f"{SN}/_all_targets.json","w",encoding="utf-8"),ensure_ascii=False)
print(f"\n[+] {len(rows)} contratos en master · {len(tgt)} con entregado pend liq>0 ({round(sum(x['pend'] for x in tgt),1)} tn)")
print("por cerealera:",Counter(x['cer'] for x in tgt if x['cer'] in {'Cargill','LDC','Bunge','Intagro','ACA','COFCO','Viterra','Allaria','Molinos','FYO','AGD'}).most_common())
