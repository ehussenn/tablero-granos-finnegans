"""Probe: aplica los filtros de Stock por Deposito (SILO, BOLSAS, SILO BOLSA, DESCARTE)
   y reporta totales por producto. Sumar siempre CANTIDAD1 (kilos) y convertir a tn."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import finnegans_api as api

r = api.call("/reports/USR_RESSTOCKDEP", {"PARAMWEBREPORT_fecha":"getCurrentDate"})
print(f"Total filas: {len(r)}")

def categorizar(dep):
    if not dep: return None
    d = dep.upper()
    if "SILO DESCARTE" in d: return "DESCARTE"
    if "SILOBOLSA" in d or "SILO BOLSA" in d: return "SILOBOLSA"
    # DEPOSITO VENTAS = bolsas/bolsones de venta
    if "DEPOSITO VENTAS" in d or "DEPÓSITO VENTAS" in d: return "BOLSAS"
    # Cualquier otro con "SILO" = SILO fisico
    if "SILO" in d: return "SILO"
    return None

# Categorizar y sumar
totales = {"SILO": {}, "SILOBOLSA": {}, "BOLSAS": {}, "DESCARTE": {}}
sin_categoria = {}
for row in r:
    dep = row.get("DEPOSITO") or ""
    cat = categorizar(dep)
    prod = row.get("PRODUCTO") or ""
    try: kg = float(row.get("CANTIDAD1") or 0)
    except: kg = 0.0
    if cat:
        totales[cat][prod] = totales[cat].get(prod, 0.0) + kg
    else:
        sin_categoria[dep] = sin_categoria.get(dep, 0) + 1

for cat in ["SILO", "SILOBOLSA", "BOLSAS", "DESCARTE"]:
    print(f"\n{'='*50}\n{cat}: {len(totales[cat])} productos")
    items = sorted(totales[cat].items(), key=lambda x: -abs(x[1]))
    # Solo mostrar los con cantidad > 0 (los relevantes)
    for p, kg in items[:15]:
        if abs(kg) > 0.01:
            print(f"   {kg/1000:>12,.2f} tn   {p}")

# Top depositos sin categoria (para verificar si nos olvidamos de algo)
print(f"\n=== Depositos NO categorizados (no van a ningun grupo) ===")
print(f"Total: {len(sin_categoria)} depositos distintos")
for dep, n in sorted(sin_categoria.items(), key=lambda x: -x[1])[:15]:
    print(f"   {n:>4}  '{dep}'")
