"""Parsea el export de Entregas de Intagro (xlsx) a data/intagro/quality.json por CTG.
Intagro expone humedad + mermas + observaciones (no el desglose de daños/quebrados)."""
import sys, json, re
from pathlib import Path
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")
import openpyxl
ROOT=Path(__file__).resolve().parent
SRC=ROOT/"scraper"/"out"/"intagro_dl"/"entregas_all.xlsx"
DATA=ROOT.parent/"data"/"intagro"; DATA.mkdir(parents=True,exist_ok=True)
wb=openpyxl.load_workbook(SRC,read_only=True); ws=wb.active
rows=list(ws.iter_rows(values_only=True))
def num(s):
    if s is None: return None
    m=re.search(r"-?\d+(?:[.,]\d+)?", str(s))
    return float(m.group(0).replace(",",".")) if m else None
out={}
for r in rows[1:]:
    cto,fecha,prod,ctg,kgdesc,hum,mermas,otras,netos,aplic,obs = (list(r)+[None]*11)[:11]
    ctg=str(ctg or "").strip()
    if not ctg or not ctg.isdigit(): continue
    out[ctg]={"ctg":ctg,"contrato":str(cto or "").strip(),"producto":str(prod or "").strip().upper(),
        "fecha":fecha,"humedad":num(hum),"mermas":num(mermas),"otras":num(otras),
        "netos":num(netos),"aplicados":num(aplic),"observaciones":str(obs or "").strip(),
        "calidad":{"HUMEDAD":num(hum)} if num(hum) else {}}
DATA.joinpath("quality.json").write_text(json.dumps(out,ensure_ascii=False),encoding="utf-8")
print(f"[+] data/intagro/quality.json: {len(out)} CTGs | {dict(Counter(v['producto'] for v in out.values()))}")
print("   con humedad>0:", sum(1 for v in out.values() if (v['humedad'] or 0)>0))
for c,v in list(out.items())[:3]: print("  ",c,v["producto"],"hum=",v["humedad"],"obs=",v["observaciones"][:30])
