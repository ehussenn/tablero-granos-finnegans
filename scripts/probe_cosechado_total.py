"""Suma kg cosechado por producto desde traslados (Traslado CPE Agronasaja + Rec Sem PROPIA)."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

r = api.call("/reports/trasladoGranos", {
    "PARAMFechaDesde": "2024-01-01",
    "PARAMFechaHasta": "2030-12-31",
})

SUBT_OK = {"Traslado CPE Agronasaja", "Recepción de Semilla PROPIA"}

# 1) Probar qué campo de producto usar — GRANO vs algún otro
# Ver qué tienen los filas de Traslado CPE Agronasaja
muestra = [row for row in r if row.get("TRANSACCIONSUBTIPONOMBRE") in SUBT_OK][:5]
print("=== 5 filas de muestra ===")
for row in muestra:
    print(f"  SUBTIPO={row.get('TRANSACCIONSUBTIPONOMBRE')[:30]} GRANO='{row.get('GRANO')}' DOCUMENTO='{row.get('DOCUMENTO')}' PESONETO={row.get('PESONETO')}")

# Sumar por GRANO
from collections import Counter
suma_grano = {}
for row in r:
    if row.get("TRANSACCIONSUBTIPONOMBRE") not in SUBT_OK: continue
    g = row.get("GRANO") or ""
    try: kg = float(row.get("PESONETO") or 0)
    except: kg = 0
    if g:
        suma_grano[g] = suma_grano.get(g, 0) + kg

print("\n=== TOTAL COSECHADO por GRANO (todos los traslados desde 2024) ===")
for g, kg in sorted(suma_grano.items(), key=lambda x: -abs(x[1])):
    print(f"   {kg/1000:>12,.2f} tn   '{g}'")

# Separar por subtipo
print("\n=== Solo Traslado CPE Agronasaja (granos) ===")
sg1 = {}
for row in r:
    if row.get("TRANSACCIONSUBTIPONOMBRE") != "Traslado CPE Agronasaja": continue
    g = row.get("GRANO") or ""
    try: kg = float(row.get("PESONETO") or 0)
    except: kg = 0
    if g: sg1[g] = sg1.get(g, 0) + kg
for g, kg in sorted(sg1.items(), key=lambda x: -abs(x[1])):
    print(f"   {kg/1000:>12,.2f} tn   '{g}'")

print("\n=== Solo Recepción de Semilla PROPIA ===")
sg2 = {}
for row in r:
    if row.get("TRANSACCIONSUBTIPONOMBRE") != "Recepción de Semilla PROPIA": continue
    g = row.get("GRANO") or ""
    try: kg = float(row.get("PESONETO") or 0)
    except: kg = 0
    if g: sg2[g] = sg2.get(g, 0) + kg
for g, kg in sorted(sg2.items(), key=lambda x: -abs(x[1])):
    print(f"   {kg/1000:>12,.2f} tn   '{g}'")
