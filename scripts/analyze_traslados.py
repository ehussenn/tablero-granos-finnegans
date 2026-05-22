"""Analizar el dataset trasladoGranos para identificar CTG y vinculo con contratos."""
import sys
sys.path.insert(0, r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts")
import finnegans_api as api
sys.stdout.reconfigure(encoding="utf-8")
from collections import Counter

d = api.call("/reports/trasladoGranos", {
    "PARAMFechaDesde": "2026-01-01",
    "PARAMFechaHasta": "2026-12-31",
})
print(f"Total filas: {len(d)}")

print("\nTRANSACCIONSUBTIPONOMBRE (tipos de operación):")
for v, n in sorted(Counter(r.get("TRANSACCIONSUBTIPONOMBRE") for r in d).items(), key=lambda x:-x[1]):
    print(f"  {n:>5}  {v!r}")

print("\nOPERACIONTIPO:")
for v, n in sorted(Counter(r.get("OPERACIONTIPO") for r in d).items(), key=lambda x:-x[1]):
    print(f"  {n:>5}  {v!r}")

# Tomar un traslado de VENTA (que tenga vinculo con contrato venta)
venta = [r for r in d if r.get("OPERACIONTIPO") == "Venta" and r.get("NOMBRECONTRATO")]
print(f"\nTraslados con OPERACIONTIPO=Venta y NOMBRECONTRATO != null: {len(venta)}")
if venta:
    print(f"\nSample VENTA con contrato (primer):")
    for k, v in venta[0].items():
        if v not in (None, "", 0, False, 0.0):
            print(f"  {k:<45} = {repr(v)[:80]}")

# Tomar un traslado de COMPRA con contrato
compra = [r for r in d if r.get("OPERACIONTIPO") == "Compra" and r.get("NOMBRECONTRATO")]
print(f"\n\nTraslados con OPERACIONTIPO=Compra y NOMBRECONTRATO != null: {len(compra)}")
if compra:
    print(f"\nSample COMPRA con contrato (primer):")
    for k, v in compra[0].items():
        if v not in (None, "", 0, False, 0.0):
            print(f"  {k:<45} = {repr(v)[:80]}")

# Buscar específicamente campos con valor "CTG" o número largo
print("\n\nCampos con datos no-null en 5 filas con NOMBRECONTRATO:")
filtered = [r for r in d if r.get("NOMBRECONTRATO")][:5]
all_cols = list(d[0].keys()) if d else []
for col in all_cols:
    vals = [r.get(col) for r in filtered]
    non_null = [v for v in vals if v not in (None, "", 0, False, 0.0)]
    if non_null:
        sample = repr(non_null[0])[:50]
        print(f"  {col:<45} = ej: {sample}")
