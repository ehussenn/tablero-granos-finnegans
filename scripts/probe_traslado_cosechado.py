"""Probe: DOCUMENTO de cada subtipo, y todos los campos que mencionen 'cosecha' o 'origen'."""
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

# Para cada subtipo, mostrar 3 ejemplos de DOCUMENTO
print("=== DOCUMENTO por TRANSACCIONSUBTIPONOMBRE ===")
by_st = {}
for row in r:
    st = row.get("TRANSACCIONSUBTIPONOMBRE") or "(sin subtipo)"
    if st not in by_st: by_st[st] = []
    if len(by_st[st]) < 3:
        by_st[st].append(row.get("DOCUMENTO"))
for st, docs in by_st.items():
    print(f"   '{st}': ej DOCUMENTO = {docs}")

# Filtros candidatos para "Traslado CPE Agronasaja" - ver todas las variantes de DOCUMENTO
print("\n=== Documentos del subtipo 'Traslado CPE Agronasaja' (prefijos únicos) ===")
prefs = Counter()
for row in r:
    if row.get("TRANSACCIONSUBTIPONOMBRE") == "Traslado CPE Agronasaja":
        doc = row.get("DOCUMENTO") or ""
        pref = doc.split(" - ")[0] if " - " in doc else doc.split()[0] if doc else ""
        prefs[pref] += 1
for p, n in prefs.most_common(15):
    print(f"   {n:>6}   '{p}'")

# Documentos del subtipo Recepcion de Semilla PROPIA
print("\n=== Documentos del subtipo 'Recepción de Semilla PROPIA' (prefijos únicos) ===")
prefs2 = Counter()
for row in r:
    if row.get("TRANSACCIONSUBTIPONOMBRE") == "Recepción de Semilla PROPIA":
        doc = row.get("DOCUMENTO") or ""
        pref = doc.split(" - ")[0] if " - " in doc else doc.split()[0] if doc else ""
        prefs2[pref] += 1
for p, n in prefs2.most_common(15):
    print(f"   {n:>6}   '{p}'")

# Para esos subtipos, mostrar todos los campos que tienen palabras "COSECHA" en algun valor
print("\n=== Campos con valor que contiene 'COSECHA' (mirando TODAS las filas) ===")
fields_with_cosecha = {}
for row in r:
    for k, v in row.items():
        if v and isinstance(v, str) and "COSECHA" in v.upper():
            fields_with_cosecha.setdefault(k, set()).add(v)
for k, vs in fields_with_cosecha.items():
    print(f"   campo '{k}': {len(vs)} valores únicos, ejemplos:")
    for v in sorted(vs)[:5]:
        print(f"      '{v}'")
