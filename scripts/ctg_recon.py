"""TAQUEO CTG: concilia Finnegans (trasladoGranos) vs los extranets scrapeados
(Cargill, LDC, Bunge, Intagro) para una VENTANA de fechas.

Lógica:
  - "vinculado" = el CTG existe en Finnegans (en cualquier fecha). Para no romper el
    match por la ventana, el chequeo de vinculación se hace contra TODO Finnegans.
  - "falta vincular" = CTG que está en el extranet de la cerealera pero NO existe en
    Finnegans en absoluto (grano que la cerealera recibió y Finnegans no tiene).
    Se acota a la ventana usando la fecha del extranet cuando existe.
  - "duplicados" = misma carta de porte (CTG) en >1 traslado de venta dentro de la ventana.
Uso: py scripts/ctg_recon.py [desde yyyy-mm-dd] [hasta yyyy-mm-dd]
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
d_desde = datetime.date.fromisoformat(DESDE)
d_hasta = datetime.date.fromisoformat(HASTA)
print(f"VENTANA: {DESDE} → {HASTA}\n")

def norm(c):
    c = re.sub(r"\D", "", str(c or ""))
    return c.lstrip("0") or ""

def parse_fecha(v):
    s = str(v or "").strip()
    if not s: return None
    s = s.split("T")[0].split(" ")[0]
    for sep in ("-", "/"):
        p = s.split(sep)
        if len(p) == 3:
            try:
                if len(p[0]) == 4: return datetime.date(int(p[0]), int(p[1]), int(p[2]))
                return datetime.date(int(p[2]), int(p[1]), int(p[0]))
            except Exception: return None
    return None

def en_ventana(v):
    d = parse_fecha(v)
    return (d is not None) and (d_desde <= d <= d_hasta)

# --- Finnegans: TODOS los traslados de venta con CTG (universo de vinculación) ---
rows = api.call("/reports/trasladoGranos", {"PARAMFechaDesde": "2024-01-01", "PARAMFechaHasta": "2030-12-31"})
rows = rows if isinstance(rows, list) else []
ventas = [r for r in rows if r.get("OPERACIONTIPO")=="Venta" and str(r.get("NUMERODOCUMENTOADICIONAL") or "").strip()]

fnn_all = defaultdict(list)          # CTG -> filas (cualquier fecha) = universo vinculado
for r in ventas:
    fnn_all[norm(r.get("NUMERODOCUMENTOADICIONAL"))].append(r)
fnn_all_set = set(k for k in fnn_all if k)

def matchCereal(rs, *keys):
    for r in rs:
        blob = f"{r.get('DESTINATARIO')} {r.get('ORGANIZACIONNOMBRE')}".upper()
        if any(k in blob for k in keys): return True
    return False

CEREALERAS = {
    "Cargill":  (["CARGILL"], "data/cargill/quality.json"),
    "LDC":      (["LDC","DREYFUS"], "data/ldc/quality.json"),
    "Bunge":    (["BUNGE"], "data/bunge/quality.json"),
    "Intagro":  (["ARGENTRADING","INTAGRO"], "data/intagro/quality.json"),
}

# --- duplicados DENTRO de la ventana ---
dups = []
for ctg, rs in fnn_all.items():
    rsv = [r for r in rs if en_ventana(r.get("FECHA"))]
    if ctg and len(rsv) > 1:
        dups.append({"ctg": ctg, "filas": len(rsv),
                     "compradores": sorted(set(str(x.get("ORGANIZACIONNOMBRE")) for x in rsv)),
                     "docs": [x.get("NUMERODOCUMENTO") for x in rsv],
                     "grano": rsv[0].get("GRANO"), "fecha": rsv[0].get("FECHA")})

n_win = sum(1 for rs in fnn_all.values() for r in rs if en_ventana(r.get("FECHA")))
print(f"Finnegans venta c/CTG: {len(fnn_all_set)} únicos (universo) · {n_win} filas en ventana · {len(dups)} DUPLICADOS en ventana\n")

out = {"ventana": [DESDE, HASTA], "duplicados": dups, "por_cerealera": {}}
for cer, (keys, path) in CEREALERAS.items():
    fp = ROOT / path
    ext = {}
    if fp.exists():
        try: ext = json.loads(fp.read_text(encoding="utf-8"))
        except Exception: ext = {}
    tiene_fecha = any(isinstance(v, dict) and (v.get("fecha")) for v in ext.values())
    ext_win = {}   # CTG norm -> registro extranet, acotado a ventana si hay fecha
    for k, v in ext.items():
        n = norm(k)
        if not n: continue
        f = (v or {}).get("fecha") if isinstance(v, dict) else None
        if tiene_fecha:
            if en_ventana(f): ext_win[n] = v
        else:
            ext_win[n] = v   # sin fecha en el extranet: no se puede acotar, se incluye
    ext_ctgs = set(ext_win.keys())
    falta_vincular = sorted(c for c in ext_ctgs if c not in fnn_all_set)   # en extranet, NO en Finnegans
    coinciden = sorted(c for c in ext_ctgs if c in fnn_all_set)
    out["por_cerealera"][cer] = {
        "extranet_en_ventana": len(ext_ctgs),
        "tiene_fecha_extranet": tiene_fecha,
        "vinculados": len(coinciden),
        "falta_vincular": [{"ctg": c, "producto": (ext_win[c].get("producto") if isinstance(ext_win[c],dict) else None),
                             "contrato": (ext_win[c].get("contrato") if isinstance(ext_win[c],dict) else None),
                             "fecha": (ext_win[c].get("fecha") if isinstance(ext_win[c],dict) else None)} for c in falta_vincular],
    }
    nota = "" if tiene_fecha else "  (extranet sin fecha: no acotado a ventana)"
    print(f"=== {cer} ==={nota}")
    print(f"  Extranet: {len(ext_ctgs)} CTGs · vinculados en Finnegans: {len(coinciden)} · FALTA VINCULAR: {len(falta_vincular)}")
    for c in falta_vincular[:25]:
        e = ext_win[c]; extra = f" · {e.get('producto')} · {e.get('fecha')}" if isinstance(e,dict) else ""
        print(f"       {c}{extra}")
    if len(falta_vincular) > 25: print(f"       ... (+{len(falta_vincular)-25})")
    print()

(ROOT/"data"/"taqueo_ctg.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[+] Guardado data/taqueo_ctg.json")
print(f"\n=== DUPLICADOS en ventana ({len(dups)}) ===")
for d in dups:
    print(f"  CTG {d['ctg']} x{d['filas']} · {d['grano']} · {d['fecha']} · {d['compradores']} · docs={d['docs']}")
