"""PENDIENTE DE LIQUIDAR (de lo entregado) por cerealera, con los números de CTG.

Fuente autoritativa: contratos de venta de Finnegans -> campo
'cantidadentregadapendienteliquidar' (tn entregadas que faltan liquidar).
Se agrupa por cerealera (comprador/destinatario obtenido de los traslados del
mismo contrato) y se listan los CTG de cada contrato pendiente.
Uso: py scripts/pendiente_liquidar.py [desde] [hasta]
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
d_desde = datetime.date.fromisoformat(DESDE); d_hasta = datetime.date.fromisoformat(HASTA)

def norm(c):
    c = re.sub(r"\D","",str(c or "")); return c.lstrip("0") or ""
def parse_fecha(v):
    s=str(v or "").split("T")[0].split(" ")[0]
    for sep in ("-","/"):
        p=s.split(sep)
        if len(p)==3:
            try:
                if len(p[0])==4: return datetime.date(int(p[0]),int(p[1]),int(p[2]))
                return datetime.date(int(p[2]),int(p[1]),int(p[0]))
            except: return None
    return None
def contnum(s):
    m = re.search(r"-\s*(\d+)", str(s or "")); return m.group(1) if m else None

def cerealera(name):
    n = str(name or "").upper()
    for k,lbl in [("CARGILL","Cargill"),("LDC","LDC"),("DREYFUS","LDC"),("BUNGE","Bunge"),
                  ("ARGENTRADING","Intagro"),("INTAGRO","Intagro"),("COFCO","COFCO"),
                  ("COOPERATIVAS ARGENTINAS","ACA"),("FYO","FYO"),("ALLARIA","Allaria"),
                  ("VITERRA","Viterra"),("ADM","ADM"),("MOLINOS","Molinos"),("AGD","AGD"),
                  ("ACEITERA GENERAL","AGD"),("CHS","CHS"),("BUNGE","Bunge")]:
        if k in n: return lbl
    return None

# --- 1) contratos venta con entregado pendiente de liquidar ---
html = (ROOT/"index.html").read_text(encoding="utf-8")
payload = json.loads(re.search(r"const PAYLOAD = (\{.*?\});", html, re.S).group(1))
ventas_ct = payload.get("pilot") or []
pend = {}
for r in ventas_ct:
    tn = r.get("cantidadentregadapendienteliquidar") or 0
    if tn and tn > 0.05:
        num = contnum(r.get("contrato"))
        gm = re.search(r"\((Grano [^)]+)\)", str(r.get("contrato") or ""))
        if num: pend[num] = {"contrato": r.get("contrato"), "grano": gm.group(1) if gm else None,
                             "tn_pend": round(tn,2), "num": num, "ctgs": [], "cerealera": None}

# --- 2) traslados venta: linkear contrato -> CTGs + cerealera (ventana amplia para captar todos) ---
rows = api.call("/reports/trasladoGranos", {"PARAMFechaDesde":"2024-01-01","PARAMFechaHasta":"2030-12-31"})
rows = rows if isinstance(rows,list) else []
for r in rows:
    if r.get("OPERACIONTIPO")!="Venta": continue
    ctg = norm(r.get("NUMERODOCUMENTOADICIONAL"))
    if not ctg: continue
    num = contnum(r.get("NOMBRECONTRATO")) or contnum(r.get("NUMERODOCUMENTOCONTRATO"))
    if num in pend:
        e = pend[num]
        d = parse_fecha(r.get("FECHA"))
        en_win = bool(d and d_desde <= d <= d_hasta)
        if ctg not in [c["ctg"] for c in e["ctgs"]]:
            e["ctgs"].append({"ctg":ctg,"fecha":r.get("FECHA"),"kg":r.get("PESONETO"),"en_ventana":en_win})
        if not e["cerealera"]:
            e["cerealera"] = cerealera(r.get("DESTINATARIO")) or cerealera(r.get("ORGANIZACIONNOMBRE"))

# --- 3) agrupar por cerealera ---
porcer = defaultdict(lambda: {"tn":0.0,"contratos":0,"ctgs":0,"detalle":[]})
sin_cer = {"tn":0.0,"contratos":0}
for num,e in pend.items():
    cer = e["cerealera"] or "(sin traslado en ventana)"
    g = porcer[cer]
    g["tn"] += e["tn_pend"]; g["contratos"] += 1; g["ctgs"] += len(e["ctgs"])
    g["detalle"].append(e)

print(f"PENDIENTE DE LIQUIDAR (entregado) · ventana {DESDE}→{HASTA}")
print(f"Total: {sum(v['tn_pend'] for v in pend.values()):,.1f} tn en {len(pend)} contratos\n")
for cer in sorted(porcer, key=lambda k: -porcer[k]["tn"]):
    g = porcer[cer]
    print(f"=== {cer} === {g['tn']:,.1f} tn · {g['contratos']} contratos · {g['ctgs']} CTGs")
    for e in sorted(g["detalle"], key=lambda x:-x["tn_pend"])[:8]:
        ctgs = ", ".join(c["ctg"] for c in e["ctgs"][:6]) + (f" (+{len(e['ctgs'])-6})" if len(e["ctgs"])>6 else "")
        print(f"   ctto {e['num']} · {e['grano']} · {e['tn_pend']} tn · CTGs: {ctgs or '—'}")
    if len(g["detalle"])>8: print(f"   ... (+{len(g['detalle'])-8} contratos)")
    print()

out = {"ventana":[DESDE,HASTA], "total_tn": round(sum(v['tn_pend'] for v in pend.values()),1),
       "por_cerealera": {k:{"tn":round(v["tn"],1),"contratos":v["contratos"],"ctgs":v["ctgs"],
                            "detalle":v["detalle"]} for k,v in porcer.items()}}
(ROOT/"data"/"pendiente_liquidar.json").write_text(json.dumps(out,ensure_ascii=False,indent=1),encoding="utf-8")
print("[+] Guardado data/pendiente_liquidar.json")
