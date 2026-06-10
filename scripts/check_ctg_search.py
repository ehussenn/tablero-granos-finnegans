"""Busca el CTG por aproximación (puede estar mal el número exacto del screenshot)."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

# Filtro mayo 2026
data = api.call("/reports/INFORMETRASGRNAPI", {
    "PARAMFechaDesde": "2026-05-01",
    "PARAMFechaHasta": "2026-05-31",
})
# Buscar CTGs que contengan "13227" o similar (en caso de que el número del screenshot esté cortado)
hits = [r for r in data if "13227" in str(r.get("CTG",""))]
print(f"CTGs que contienen '13227' en mayo 2026: {len(set(r.get('CTG') for r in hits))}")
for ctg in sorted(set(r.get("CTG") for r in hits)):
    rows = [r for r in hits if r.get("CTG") == ctg]
    print(f"\n>>> CTG {ctg} ({len(rows)} filas):")
    for r in rows:
        print(f"  ORG={r.get('ORGANIZACION'):40s} | SOL={r.get('SOLICITANTE'):40s} | CONTRATO={r.get('CONTRATO')!r}")

# También buscar por organización LDC / BENAYAS para verificar
print("\n\nBúsqueda por BENAYAS o LDC en mayo:")
b_or_ldc = [r for r in data if "BENAYAS" in (r.get("ORGANIZACION") or "").upper() or
                                ("LDC" in (r.get("ORGANIZACION") or "").upper() and r.get("CONTRATO","").startswith("CTO"))]
ctgs_uniq = sorted(set(r.get("CTG") for r in b_or_ldc))
print(f"CTGs únicos: {len(ctgs_uniq)}")
for ctg in ctgs_uniq[:10]:
    rows = [r for r in b_or_ldc if r.get("CTG") == ctg]
    print(f"  CTG {ctg}: {len(rows)} filas")
    for r in rows:
        print(f"    ORG={r.get('ORGANIZACION'):40s} | CONTRATO={r.get('CONTRATO')!r}")
