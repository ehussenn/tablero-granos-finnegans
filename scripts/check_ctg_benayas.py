"""Debug: ver qué devuelve INFORMETRASGRNAPI para CTG 1013227524 (BENAYAS / LDC)."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

# 1) INFORMETRASGRNAPI con filtro de fecha
print("="*100); print("INFORMETRASGRNAPI — filas con CTG 1013227524"); print("="*100)
data = api.call("/reports/INFORMETRASGRNAPI", {
    "PARAMFechaDesde": "2026-05-01",
    "PARAMFechaHasta": "2026-05-31",
})
rows = [r for r in data if str(r.get("CTG","")) == "1013227524"]
print(f"Filas para ese CTG: {len(rows)}")
for i, r in enumerate(rows):
    print(f"\n--- Fila {i+1} ---")
    for k, v in r.items():
        print(f"  {k:<30} = {repr(v)[:80]}")

# 2) cartaPortePorCTG (más detallado)
print("\n\n" + "="*100); print("cartaPortePorCTG (CTG=1013227524)"); print("="*100)
data2 = api.call("/reports/cartaPortePorCTG", {"CTG": "1013227524"})
print(f"Filas: {len(data2) if isinstance(data2, list) else 'no list'}")
if isinstance(data2, list):
    for i, r in enumerate(data2):
        print(f"\n--- Fila {i+1} ---")
        for k, v in r.items():
            print(f"  {k:<30} = {repr(v)[:80]}")

# 3) También chequeo CTG LAMBERTUCCI (el que funcionó) para comparar
print("\n\n" + "="*100); print("INFORMETRASGRNAPI — LAMBERTUCCI 10128658870 (comparar)"); print("="*100)
data3 = api.call("/reports/INFORMETRASGRNAPI", {
    "PARAMFechaDesde": "2026-01-01",
    "PARAMFechaHasta": "2026-01-31",
})
rows3 = [r for r in data3 if str(r.get("CTG","")) == "10128658870"]
print(f"Filas: {len(rows3)}")
for i, r in enumerate(rows3):
    print(f"\n--- Fila {i+1} ---")
    print(f"  ORGANIZACION = {r.get('ORGANIZACION')}")
    print(f"  SOLICITANTE  = {r.get('SOLICITANTE')}")
    print(f"  CONTRATO     = {r.get('CONTRATO')}")

# Cuento patrones de CONTRATO en TODA la base
print("\n\n" + "="*100); print("Patrones de CONTRATO en INFORMETRASGRNAPI"); print("="*100)
from collections import Counter
data_all = api.call("/reports/INFORMETRASGRNAPI", {
    "PARAMFechaDesde": "2024-01-01",
    "PARAMFechaHasta": "2030-12-31",
})
pats = Counter()
for r in data_all:
    c = (r.get("CONTRATO") or "").strip()
    if not c: pats["(vacio)"] += 1
    elif c.startswith("COMP"): pats["COMPxxx"] += 1
    elif c.startswith("VEN"): pats["VENxxx"] += 1
    elif "CPRA" in c.upper(): pats[c.split("-")[0]+"... (CPRA)"] += 1
    elif "VTA" in c.upper(): pats[c.split("-")[0]+"... (VTA)"] += 1
    elif "CONT" in c.upper(): pats[c.split("-")[0]+"... (CONT-other)"] += 1
    else: pats["OTRO: "+(c[:30])] += 1
for k, v in pats.most_common():
    print(f"  {v:>6}  {k}")
