"""Parsea el archivo Analisis.anali (ancho fijo, exportado de Bunge operacionesbasa)
a data/bunge/quality.json keyed por CTG: producto, fecha, honorariosCamara, calidad{}."""
import sys, json, re
from pathlib import Path
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
SRC=ROOT/"scraper"/"out"/"bunge_dl"/"Analisis.anali"
DATA=ROOT.parent/"data"/"bunge"; DATA.mkdir(parents=True,exist_ok=True)
t=SRC.read_bytes().decode("latin-1").replace("\\","ñ")  # la fuente usa \ por ñ
GRAINS={"GIRASOL","MAIZ","SOJA","TRIGO","SORGO","CEBADA"}
norm=lambda s: re.sub(r"\s+"," ",s).strip()
out={}; bad=Counter()
for ln in t.splitlines():
    if not ln.strip(): continue
    ctg_m=re.search(r"\b(0\d{11})\b", ln)
    if not ctg_m: continue
    ctg=ctg_m.group(1)
    prod=next((g for g in GRAINS if g in ln), None)
    fecha_m=re.search(r"(\d{2}/\d{2}/\d{4})", ln)
    fecha=fecha_m.group(1) if fecha_m else None
    tail=ln[ctg_m.end():]
    # desc + valor + kg al final (desc = texto no numérico)
    tm=re.search(r"([A-Za-zñÑáéíóúÁ\. ]+?)\s+(\d{1,3}(?:\.\d{3})*,\d+|\d+,\d+|\d+)\s+(\d+)\s*$", tail)
    if not tm: bad[norm(tail)[:30]]+=1; continue
    desc=re.sub(r"^[VN]\s+","",norm(tm.group(1))); valor=tm.group(2).replace(".","").replace(",",".")
    try: valf=float(valor)
    except: valf=None
    hon_m=re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s+[VN ]?\s*"+re.escape(tm.group(1).strip()[:6]), tail)
    e=out.setdefault(ctg,{"ctg":ctg,"producto":prod,"fecha":fecha,
        "honorariosCamara":(hon_m.group(1).replace(".","").replace(",",".") if hon_m else None),"calidad":{}})
    if prod and not e["producto"]: e["producto"]=prod
    e["calidad"][desc]=valf
(DATA/"quality.json").write_text(json.dumps(out,ensure_ascii=False),encoding="utf-8")
print(f"[+] data/bunge/quality.json: {len(out)} CTGs | {dict(Counter(v['producto'] for v in out.values()))}")
for ctg,v in list(out.items())[:2]:
    print("  ",ctg,v["producto"],v["fecha"],"hon=",v["honorariosCamara"],"\n     cal=",v["calidad"])
if bad: print("   no parseadas:", bad.most_common(4))
