"""SEGUIMIENTO FINO Finnegans por grano (Soja / Maíz / Trigo Pan), dos flujos:
  - PROPIO (sale de campo): Traslado CPE Agronasaja  -> producción propia
  - CONSIGNACIÓN (pasaje): Recepción COMPRA CV  ->  Traslado VENTA CV (misma carta)
Calcula por grano: CTGs, tn, duplicados, y el CRUCE compra↔venta (descalces).
Guarda data/seguimiento_ctg.json.  Uso: py scripts/seguimiento_ctg.py [desde] [hasta]
"""
import sys, json, re, datetime
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import finnegans_api as api
ROOT = Path(__file__).resolve().parent.parent

DESDE = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
HASTA = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
d0 = datetime.date.fromisoformat(DESDE); d1 = datetime.date.fromisoformat(HASTA)

def norm(c): c = re.sub(r"\D","",str(c or "")); return c.lstrip("0") or ""
def pf(v):
    s=str(v or "").split("T")[0].split(" ")[0]
    for sep in ("-","/"):
        p=s.split(sep)
        if len(p)==3:
            try:
                if len(p[0])==4: return datetime.date(int(p[0]),int(p[1]),int(p[2]))
                return datetime.date(int(p[2]),int(p[1]),int(p[0]))
            except: return None
    return None
def gr(g):
    g=str(g or "").lower()
    if "soja" in g and "sem" not in g: return "Soja"
    if ("maíz" in g or "maiz" in g) and not any(x in g for x in ("sem","pising","blanco","oleico")): return "Maíz"
    if "trigo" in g and "sem" not in g: return "Trigo Pan"
    return None
FLUJO = {
    "Traslado CPE Agronasaja": "propio",
    "Recepción de Granos COMPRA CV": "compra",
    "Traslado de Granos VENTA CV": "venta",
}

rows = api.call("/reports/trasladoGranos", {"PARAMFechaDesde":DESDE,"PARAMFechaHasta":HASTA})
rows = rows if isinstance(rows,list) else []
rows = [r for r in rows if (lambda d: d and d0<=d<=d1)(pf(r.get("FECHA")))]

# estructura: grano -> flujo -> ctg -> lista de filas
data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
for r in rows:
    g = gr(r.get("GRANO")); fl = FLUJO.get(r.get("TRANSACCIONSUBTIPONOMBRE"))
    if not g or not fl: continue
    ctg = norm(r.get("NUMERODOCUMENTOADICIONAL"))
    if not ctg: continue
    data[g][fl][ctg].append(r)

out = {"ventana":[DESDE,HASTA], "granos":{}}
def kg(rs): return round(sum(float(x.get("PESONETO") or 0) for x in rs)/1000.0, 1)

print(f"SEGUIMIENTO FINO · {DESDE}→{HASTA}\n")
for g in ["Soja","Maíz","Trigo Pan"]:
    gd = data.get(g, {})
    propio = gd.get("propio", {}); compra = gd.get("compra", {}); venta = gd.get("venta", {})
    dup_propio = {c:v for c,v in propio.items() if len(v)>1}
    dup_compra = {c:v for c,v in compra.items() if len(v)>1}
    dup_venta  = {c:v for c,v in venta.items()  if len(v)>1}
    compra_sin_venta = sorted(set(compra) - set(venta))
    venta_sin_compra = sorted(set(venta) - set(compra))
    cierran = sorted(set(compra) & set(venta))
    tn_all = lambda d: round(sum(kg(v) for v in d.values()),1)
    out["granos"][g] = {
        "propio":  {"ctgs":len(propio),  "tn":tn_all(propio),  "duplicados":sorted(dup_propio)},
        "compra":  {"ctgs":len(compra),  "tn":tn_all(compra),  "duplicados":sorted(dup_compra)},
        "venta":   {"ctgs":len(venta),   "tn":tn_all(venta),   "duplicados":sorted(dup_venta)},
        "consignacion_cierran": len(cierran),
        "compra_sin_venta": compra_sin_venta,
        "venta_sin_compra": venta_sin_compra,
    }
    print(f"=== {g} ===")
    print(f"  SALE DE CAMPO (propio/CPE): {len(propio)} CTGs · {tn_all(propio):,.1f} tn" + (f" · ⚠{len(dup_propio)} duplicados" if dup_propio else ""))
    print(f"  CONSIGNACIÓN compra CV: {len(compra)} CTGs · {tn_all(compra):,.1f} tn" + (f" · ⚠{len(dup_compra)} dup" if dup_compra else ""))
    print(f"  CONSIGNACIÓN venta  CV: {len(venta)} CTGs · {tn_all(venta):,.1f} tn" + (f" · ⚠{len(dup_venta)} dup" if dup_venta else ""))
    print(f"  cierran compra↔venta: {len(cierran)}  |  ⚠ compra SIN venta: {len(compra_sin_venta)}  |  ⚠ venta SIN compra: {len(venta_sin_compra)}")
    if compra_sin_venta[:6]: print(f"     compra sin venta: {compra_sin_venta[:6]}")
    if venta_sin_compra[:6]: print(f"     venta sin compra: {venta_sin_compra[:6]}")
    print()

(ROOT/"data"/"seguimiento_ctg.json").write_text(json.dumps(out,ensure_ascii=False,indent=1),encoding="utf-8")
print("[+] Guardado data/seguimiento_ctg.json")
