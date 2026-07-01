"""Explora los CTG de Finnegans (trasladoGranos) para la conciliación vs extranets:
- compradores/destinatarios (para mapear a cada cerealera)
- CTGs duplicados
- normalización de CTG"""
import sys, json, re
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import finnegans_api as api

rows = api.call("/reports/trasladoGranos", {"PARAMFechaDesde":"2024-01-01","PARAMFechaHasta":"2030-12-31"})
rows = rows if isinstance(rows, list) else []
def norm(c):
    c = re.sub(r"\D","", str(c or ""))
    return c.lstrip("0") or ""
# solo movimientos con CTG (NUMERODOCUMENTOADICIONAL)
conctg = [r for r in rows if str(r.get("NUMERODOCUMENTOADICIONAL") or "").strip()]
print(f"traslados totales={len(rows)}  con CTG={len(conctg)}")
print("\n=== subtipos de los que tienen CTG ===")
for k,v in Counter(str(r.get("TRANSACCIONSUBTIPONOMBRE")) for r in conctg).most_common(): print(f"  {v:5} {k}")
print("\n=== compradores (ORGANIZACIONNOMBRE) en VENTA con CTG ===")
ventas = [r for r in conctg if r.get("OPERACIONTIPO")=="Venta"]
for k,v in Counter(str(r.get("ORGANIZACIONNOMBRE")) for r in ventas).most_common(20): print(f"  {v:5} {k}")
print("\n=== DESTINATARIO en VENTA con CTG ===")
for k,v in Counter(str(r.get("DESTINATARIO")) for r in ventas).most_common(20): print(f"  {v:5} {k}")
# duplicados de CTG (mismo CTG en varias filas del mismo OPERACIONTIPO)
print("\n=== CTGs duplicados (misma carta en >1 fila de venta) ===")
by = defaultdict(list)
for r in ventas: by[norm(r.get("NUMERODOCUMENTOADICIONAL"))].append(r)
dups = {k:v for k,v in by.items() if k and len(v)>1}
print(f"  CTGs venta únicos={len(by)}  duplicados={len(dups)}")
for k,v in list(dups.items())[:8]:
    print(f"   CTG {k}: {len(v)} filas | compradores={set(x.get('ORGANIZACIONNOMBRE') for x in v)} | docs={[x.get('NUMERODOCUMENTO') for x in v]}")
