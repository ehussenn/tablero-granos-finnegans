"""Lista TODOS los depositos donde hay Grano Soja, con cantidad y categoria que le asigno."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import finnegans_api as api

r = api.call("/reports/USR_RESSTOCKDEP", {"PARAMWEBREPORT_fecha":"getCurrentDate"})

def categorizar(dep):
    if not dep: return "(sin dep)"
    d = dep.upper()
    if "SILO DESCARTE" in d: return "DESCARTE"
    if "SILOBOLSA" in d or "SILO BOLSA" in d: return "SILOBOLSA"
    if "DEPOSITO VENTAS" in d or "DEPÓSITO VENTAS" in d: return "BOLSAS"
    if "SILO" in d: return "SILO"
    return "OTRO"

soja = [r for r in r if (r.get("PRODUCTO") or "").strip() == "Grano Soja"]
print(f"Total filas con Grano Soja: {len(soja)}")

# agrupar por (categoria, deposito)
agg = {}
for row in soja:
    dep = row.get("DEPOSITO") or "(sin dep)"
    cat = categorizar(dep)
    try: kg = float(row.get("CANTIDAD1") or 0)
    except: kg = 0
    if (cat, dep) not in agg:
        agg[(cat, dep)] = 0
    agg[(cat, dep)] += kg

# Resumen por categoria
print("\n=== Totales Grano Soja por categoria ===")
tot_cat = {}
for (cat, dep), kg in agg.items():
    tot_cat[cat] = tot_cat.get(cat, 0) + kg
for cat, kg in sorted(tot_cat.items(), key=lambda x: -abs(x[1])):
    print(f"   {kg/1000:>10,.2f} tn   {cat}")

# Detalle por deposito SILOBOLSA
print("\n=== Detalle Grano Soja en SILOBOLSA (mi filtro) ===")
sb = sorted([(d,k) for (c,d),k in agg.items() if c=="SILOBOLSA"], key=lambda x: -abs(x[1]))
total_sb = 0
for dep, kg in sb:
    print(f"   {kg/1000:>10,.2f} tn   {dep}")
    total_sb += kg
print(f"   {'TOTAL':>10}   {total_sb/1000:,.2f} tn")

# Detalle en SILO (silos físicos)
print("\n=== Detalle Grano Soja en SILO (silos físicos) ===")
si = sorted([(d,k) for (c,d),k in agg.items() if c=="SILO"], key=lambda x: -abs(x[1]))
total_si = 0
for dep, kg in si:
    if kg:
        print(f"   {kg/1000:>10,.2f} tn   {dep}")
        total_si += kg
print(f"   {'TOTAL':>10}   {total_si/1000:,.2f} tn")

# OTROS (no clasificados)
print("\n=== Detalle Grano Soja en OTROS / BOLSAS / DESCARTE ===")
for cat in ["BOLSAS","DESCARTE","OTRO"]:
    lst = sorted([(d,k) for (c,d),k in agg.items() if c==cat], key=lambda x: -abs(x[1]))
    if lst:
        print(f"   --- {cat} ---")
        for dep, kg in lst:
            if kg:
                print(f"      {kg/1000:>10,.2f} tn   {dep}")
