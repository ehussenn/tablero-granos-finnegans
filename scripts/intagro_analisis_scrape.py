"""Baja el análisis COMPLETO por CTG de Intagro: por cada contrato 25/26 hace
POST VerContratoAmpliado.php y parsea la solapa Análisis (Rubro/Resultado por
Nº Comprobante=CTG). Mergea a data/intagro/quality.json con rubros estándar."""
import sys, re, json
from pathlib import Path
from collections import Counter
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
DATA=ROOT.parent/"data"/"intagro"; QF=DATA/"quality.json"
PROD="2503"
CODE2NAME={"HD":"HUMEDAD","HU":"HUMEDAD","DA":"GRANOS DAÑADOS","GQ":"GRANOS QUEBRADOS",
    "CE":"CUERPOS EXTRAÑOS","PH":"PESO HECTOLITRICO","GR":"GRADO","MG":"MATERIA GRASA",
    "VE":"GRANOS VERDES","PI":"GRANOS PICADOS","GP":"GRANOS PICADOS","AC":"ACIDEZ",
    "MV":"MERMA VOLATIL","PR":"PROTEINA","PT":"PROTEINA","CH":"CHAMICO","MO":"GRANOS AMOHOSADOS",
    "OL":"OLOR"}
SKIP={"CO"}  # "Conforme": no es un rubro de calidad
def fnum(s):
    m=re.search(r"-?\d+(?:,\d+)?", s or ""); return float(m.group(0).replace(",",".")) if m else None
def main():
    q=json.loads(QF.read_text(encoding="utf-8")) if QF.exists() else {}
    contratos=sorted(set(v["contrato"] for v in q.values() if v.get("contrato")))
    print(f"[+] {len(contratos)} contratos a procesar")
    codes=Counter(); touched=set()
    with sync_playwright() as p:
        b=p.chromium.connect_over_cdp("http://localhost:9334")
        ctx=b.contexts[0]; page=ctx.pages[-1]
        for i,c in enumerate(contratos,1):
            try:
                r=page.request.post("https://portal.intagro.com/ajax_altocom/VerContratoAmpliado.php",
                    form={"productor":PROD,"contrato":c,"areanegocio":"GV"},
                    headers={"X-Requested-With":"XMLHttpRequest"}, timeout=40000)
                html=r.text()
            except Exception as e: print(f"  [!] {c}: {str(e)[:50]}"); continue
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S|re.I):
                cells=[re.sub(r"<[^>]+>","",x).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S|re.I)]
                if len(cells)>=7 and re.match(r"\d{2}/\d{2}/\d{4}",cells[0]) and re.match(r"^\d{11}$",cells[2]) and re.match(r"^[A-Z]{2}$",cells[3]):
                    ctg, code, res = cells[2], cells[3], fnum(cells[4])
                    if code in SKIP: continue
                    codes[code]+=1
                    e=q.get(ctg)
                    if not e:
                        e=q[ctg]={"ctg":ctg,"contrato":c,"producto":None,"calidad":{}}
                    name=CODE2NAME.get(code, code)
                    e.setdefault("calidad",{})[name]=res   # valor medido (Resultado)
                    touched.add(ctg)
            if i%10==0: print(f"  ... {i}/{len(contratos)}")
        b.close()
    QF.write_text(json.dumps(q,ensure_ascii=False),encoding="utf-8")
    print(f"[+] CTGs con rubros de análisis: {len(touched)}")
    print(f"[+] códigos de rubro encontrados: {dict(codes)}")
    return 0
sys.exit(main())
