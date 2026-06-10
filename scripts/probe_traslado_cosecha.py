"""Probe: estructura del reporte de traslados; filtrar por TRAS-VTA-GRANO-AS y REC-SEM-PPIO con origen DEP COSECHA."""
import sys
from pathlib import Path
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

r = api.call("/reports/trasladoGranos", {
    "PARAMFechaDesde": "2024-01-01",
    "PARAMFechaHasta": "2030-12-31",
})
print(f"Total filas traslados: {len(r)}")
if not r:
    print("Sin datos"); sys.exit()

# Campos disponibles
print(f"\nCampos disponibles ({len(r[0])}):")
for k in sorted(r[0].keys()):
    print(f"   {k}")

# Buscar campos que tengan TRAS-VTA-GRANO-AS, REC-SEM-PPIO o DEP COSECHA en valores
print("\n=== Valores que contienen 'TRAS-VTA-GRANO-AS' ===")
for k in r[0].keys():
    vals = set()
    for row in r[:500]:
        v = row.get(k)
        if v and isinstance(v, str) and "TRAS-VTA-GRANO-AS" in v:
            vals.add(v)
        if len(vals) > 5: break
    if vals:
        print(f"   campo '{k}': ej={list(vals)[:3]}")

print("\n=== Valores que contienen 'REC-SEM-PPIO' ===")
for k in r[0].keys():
    vals = set()
    for row in r[:500]:
        v = row.get(k)
        if v and isinstance(v, str) and "REC-SEM-PPIO" in v:
            vals.add(v)
        if len(vals) > 5: break
    if vals:
        print(f"   campo '{k}': ej={list(vals)[:3]}")

print("\n=== Valores que contienen 'DEP COSECHA' o 'COSECHA' ===")
for k in r[0].keys():
    vals = set()
    for row in r[:500]:
        v = row.get(k)
        if v and isinstance(v, str) and "DEP COSECHA" in v.upper():
            vals.add(v)
        if len(vals) > 4: break
    if vals:
        print(f"   campo '{k}': ej={list(vals)[:3]}")

# Subtipos de traslados (TRANSACCIONSUBTIPONOMBRE)
print(f"\n=== Subtipos de transacción ===")
subt = Counter(row.get("TRANSACCIONSUBTIPONOMBRE") for row in r)
for s, n in subt.most_common(15):
    print(f"   {n:>6}  '{s}'")
