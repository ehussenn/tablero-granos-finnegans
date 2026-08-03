"""Lista depositos en el stock por deposito (para encontrar los silo-bolsa)."""
import sys, json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import finnegans_api as api

print("[+] Bajando stock por deposito...", flush=True)
r = api.call("/reports/USR_RESSTOCKDEP", {"PARAMWEBREPORT_fecha":"getCurrentDate"})
print(f"    -> {len(r)} filas", flush=True)

# Listar depositos unicos y contar
deps = {}
for row in r:
    d = row.get("DEPOSITO") or ""
    deps[d] = deps.get(d, 0) + 1

# Mostrar depositos que contienen 'silo' o 'bolsa'
print("\n[+] Depositos relacionados con 'silo' o 'bolsa':", flush=True)
for d, n in sorted(deps.items(), key=lambda x: -x[1]):
    dl = d.lower()
    if "silo" in dl or "bolsa" in dl:
        print(f"  {n:5} filas - '{d}'", flush=True)

print("\n[+] Top 20 depositos por filas:", flush=True)
for d, n in sorted(deps.items(), key=lambda x: -x[1])[:20]:
    print(f"  {n:5} filas - '{d}'", flush=True)

# Para los que tienen 'silo bolsa', mostrar productos y cantidades
print("\n[+] Stock en SILO BOLSA (productos y cantidades):", flush=True)
sb_rows = [row for row in r if "silo bolsa" in (row.get("DEPOSITO") or "").lower()]
print(f"    -> {len(sb_rows)} filas en silo bolsa", flush=True)
# agrupar por producto
by_prod = {}
for row in sb_rows:
    p = row.get("PRODUCTO") or "?"
    c1 = row.get("CANTIDAD1") or 0
    by_prod[p] = by_prod.get(p, 0) + (float(c1) if c1 else 0)
for p, q in sorted(by_prod.items(), key=lambda x: -x[1])[:25]:
    print(f"  {q:14,.2f}  {p}", flush=True)
