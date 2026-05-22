"""Buscar CTG en los traslados — especificamente en CPE Agronasaja y Recepcion Granos COMPRA CV."""
import sys
sys.path.insert(0, r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts")
import finnegans_api as api
sys.stdout.reconfigure(encoding="utf-8")

d = api.call("/reports/trasladoGranos", {
    "PARAMFechaDesde": "2026-01-01",
    "PARAMFechaHasta": "2026-12-31",
})

# 1) Sample CPE Agronasaja
cpe = [r for r in d if r.get("TRANSACCIONSUBTIPONOMBRE") == "Traslado CPE Agronasaja"]
print(f"\n=== 'Traslado CPE Agronasaja': {len(cpe)} traslados ===")
if cpe:
    print(f"\nSample CPE Agronasaja - TODOS los campos no null:")
    for k, v in cpe[0].items():
        if v not in (None, "", 0, False, 0.0):
            print(f"  {k:<45} = {repr(v)[:100]}")

# 2) Sample Recepción COMPRA CV
rec = [r for r in d if r.get("TRANSACCIONSUBTIPONOMBRE") == "Recepción de Granos COMPRA CV"]
print(f"\n=== 'Recepción de Granos COMPRA CV': {len(rec)} ===")
if rec:
    print(f"\nSample Recepción COMPRA CV (primer no-null):")
    for k, v in rec[0].items():
        if v not in (None, "", 0, False, 0.0):
            print(f"  {k:<45} = {repr(v)[:100]}")

# 3) Sample Traslado Venta CV
tve = [r for r in d if r.get("TRANSACCIONSUBTIPONOMBRE") == "Traslado de Granos VENTA CV"]
print(f"\n=== 'Traslado de Granos VENTA CV': {len(tve)} ===")
if tve:
    print(f"\nSample VENTA CV (primer no-null):")
    for k, v in tve[0].items():
        if v not in (None, "", 0, False, 0.0):
            print(f"  {k:<45} = {repr(v)[:100]}")

# 4) Es el "DOCUMENTO" el formato CTG?
print(f"\n\n=== Sample de DOCUMENTOs en cada subtipo ===")
from collections import defaultdict
docs_by_subt = defaultdict(list)
for r in d:
    st = r.get("TRANSACCIONSUBTIPONOMBRE")
    docs_by_subt[st].append(r.get("DOCUMENTO"))
for st, docs in docs_by_subt.items():
    samples = list(set(docs))[:3]
    print(f"  {st}: {samples}")

# 5) Buscar campos cuyo valor numerico tenga 8+ digitos (probable CTG)
print(f"\n=== Buscando campos con numeros largos (CTG tiene 8+ digitos) ===")
if cpe:
    for k, v in cpe[0].items():
        s = str(v) if v is not None else ""
        if len(s) >= 8 and s.isdigit():
            print(f"  ★ {k}: {v}")
        elif "00" in s and len(s) >= 10:
            print(f"  ? {k}: {v}")
