"""Segunda pasada del taqueo: los CTG de CONSIGNACIÓN (CV) se liquidan por el lado de
COMPRA (LiquidacionGranosCompraVO), no en el contrato de venta. Chequea las liquidaciones
de los contratos de compra CV y quita de 'sin liquidar' los que ya estén liquidados ahí."""
import sys,re,json
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
SN="scripts/scraper/out/finn_sniff"
def norm(c): return re.sub(r'\D','',str(c or '')).lstrip('0')
# 1) mapa ctg -> compra contract num (consignación)
cv=json.load(open(f"{SN}/_compra_cv_map.json",encoding='utf-8'))["compra_cv"]
# 2) mapa compra contract num -> CONTRATOID (desde payload live)
h=open(r'C:\Users\ezequ\AppData\Local\Temp\claude\C--Users-ezequ\2e1e039f-2f92-42e9-a98e-c74855e039f1\scratchpad\live7.html',encoding='utf-8',errors='replace').read()
i=h.find('const PAYLOAD = ')+len('const PAYLOAD = ')
P,_=json.JSONDecoder().raw_decode(h[i:])
num2cid={}
for c in P.get('compra',[]):
    ni=str(c.get('numerointerno') or '')
    if ni: num2cid[ni]=str(c.get('contratoid'))
contratos=sorted(set(v for v in cv.values() if v))
print(f"{len(cv)} CTG consignación · {len(contratos)} contratos compra a chequear")
BSA="https://oneteam.finneg.com/BSA"
GRID=BSA+"/webreport/data?layout=WebReportGridLayout&masterWR=0&custom=1&clazz=faf.client.ui.WidgetGrid2&method=dataMethod&"
def body(method,pk):
    return ('<?xml version="1.0" encoding="UTF-8"?><postData><userData><![CDATA[pkField=TransaccionID\n'
            f'class=app.ceres.transacciones.comercializacion.contratos.granos.ContratoGranosHLP\nmethod={method}\npk={pk}\n]]></userData><parameters></parameters></postData>')
hdr={"content-type":"application/x-www-form-urlencoded; charset=UTF-8"}
liq_compra=set()
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    page=b.contexts[0].pages[0]
    for k,num in enumerate(contratos,1):
        cid=num2cid.get(num)
        if not cid: print(f"  [{k}] compra {num}: sin CONTRATOID"); continue
        try:
            liq=page.request.post(GRID,data=body("getAjaxResponseForGrillaRefreshLiquidaciones",cid),headers=hdr,timeout=60000).text()
            ml=re.search(r'<data><!\[CDATA\[(.*?)\]\]>',liq,re.S)
            pks=[r.split(',')[0].strip() for r in (ml.group(1).split(';') if ml else []) if r.split(',')[0].strip().isdigit()]
            for pk in pks:
                d=page.request.get(f"{BSA}/standardDF_def_and_data?standardXml=claseVO=LiquidacionGranosCompraVO&pk={pk}&fromDFVIEWER=1",timeout=60000).text()
                liq_compra|=set(norm(x) for x in re.findall(r'<Descripcion[^>]*>(\d{11})/',d))
        except Exception as e:
            print(f"  [{k}] compra {num} (cid {cid}): ERR {str(e)[:40]}")
        if k%10==0: print(f"  ...{k}/{len(contratos)} · liquidados compra acum {len(liq_compra)}")
    b.close()
json.dump(sorted(liq_compra),open(f"{SN}/_liq_compra_cv.json","w"))
# 3) aplicar: quitar de sin_liquidar los que estén liquidados en compra
d=json.load(open(f"{SN}/_taqueo_all.json",encoding='utf-8'))
quitados=0
for r in d:
    if 'sin_liquidar_ctg' not in r: continue
    keep=[c for c in r['sin_liquidar_ctg'] if c not in liq_compra]
    quitados+=len(r['sin_liquidar_ctg'])-len(keep)
    r['sin_liquidar_ctg']=keep
    if 'detalle' in r: r['detalle']=[x for x in r['detalle'] if x['ctg'] in keep or not x.get('ok_11d',True)]
    r['tn_sin_liquidar']=round(sum(x['tn'] for x in r.get('detalle',[]) if x['ctg'] in keep),2)
json.dump(d,open(f"{SN}/_taqueo_all.json","w",encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n[+] CTG liquidados en compra CV: {len(liq_compra)} · quitados de sin-liquidar: {quitados}")
