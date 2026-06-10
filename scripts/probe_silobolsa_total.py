"""Probe consolidado: total en silo bolsa por producto, unidad de medida."""
import sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import finnegans_api as api

r = api.call("/reports/USR_RESSTOCKDEP", {"PARAMWEBREPORT_fecha":"getCurrentDate"})
print(f"Total filas: {len(r)}")

# patron silo bolsa: "SILOBOLSA" o "SILO BOLSA"
def is_silobolsa(dep):
    if not dep: return False
    d = dep.upper()
    return "SILOBOLSA" in d or "SILO BOLSA" in d

sb = [row for row in r if is_silobolsa(row.get("DEPOSITO"))]
print(f"\nFilas silo bolsa: {len(sb)}")

# unidades unicas
unids = {}
for row in sb:
    u = (row.get("UNIDAD1") or "").strip()
    unids[u] = unids.get(u, 0) + 1
print(f"Unidades UNIDAD1: {unids}")

# Por producto (en CANTIDAD1)
byp = {}
for row in sb:
    p = row.get("PRODUCTO") or "?"
    c = row.get("CANTIDAD1") or 0
    try: c = float(c)
    except: c = 0
    byp[p] = byp.get(p, 0) + c

print("\n=== Stock total por producto en silo bolsa (CANTIDAD1) ===")
for p, q in sorted(byp.items(), key=lambda x: -abs(x[1])):
    if q != 0:
        print(f"  {q:18,.2f}  {p}")

# Totales por familia (soja/maiz/trigo simplificado)
fam = {"SOJA":0, "MAIZ":0, "TRIGO":0, "GIRASOL":0, "SORGO":0, "OTROS":0}
for p, q in byp.items():
    pl = p.lower()
    if "soja" in pl: fam["SOJA"] += q
    elif "maiz" in pl or "maíz" in pl: fam["MAIZ"] += q
    elif "trigo" in pl or "triticale" in pl: fam["TRIGO"] += q
    elif "girasol" in pl: fam["GIRASOL"] += q
    elif "sorgo" in pl: fam["SORGO"] += q
    else: fam["OTROS"] += q
print("\n=== Totales por familia (CANTIDAD1) ===")
for f, t in fam.items():
    print(f"  {f:8}: {t:18,.2f}")
