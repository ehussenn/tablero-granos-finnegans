"""Explora /reports/trasladoGranos: subtipos, campos, y arma un taqueo preliminar
de traslados que SALEN DE CAMPO vs Carta de Porte / CTG."""
import sys, json
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import finnegans_api as api

rows = api.call("/reports/trasladoGranos", {
    "PARAMFechaDesde": "2025-01-01",
    "PARAMFechaHasta": "2030-12-31",
})
if not isinstance(rows, list):
    print("respuesta no es lista:", type(rows), str(rows)[:200]); sys.exit(1)
print(f"TOTAL traslados: {len(rows)}")
print("\n=== columnas disponibles ===")
if rows: print(sorted(rows[0].keys()))
print("\n=== subtipos (TRANSACCIONSUBTIPONOMBRE) ===")
for k,v in Counter(str(r.get("TRANSACCIONSUBTIPONOMBRE")) for r in rows).most_common(): print(f"  {v:5}  {k}")
print("\n=== OPERACIONTIPO ===")
for k,v in Counter(str(r.get("OPERACIONTIPO")) for r in rows).most_common(): print(f"  {v:5}  {k}")
# posibles campos de origen/destino deposito
print("\n=== claves que huelen a origen/destino/deposito/campo ===")
if rows:
    for kk in sorted(rows[0].keys()):
        if any(s in kk.upper() for s in ["ORIGEN","DESTINO","DEPOSITO","CAMPO","ESTABLEC","ADICIONAL","DOCUMENTO","ESTADO","CTG","PORTE"]):
            print(f"  {kk} = {rows[0].get(kk)!r}")
print("\n=== 2 filas de ejemplo (subtipo traslado CPE si hay) ===")
ej = [r for r in rows if "CPE" in str(r.get("TRANSACCIONSUBTIPONOMBRE",""))][:2] or rows[:2]
for r in ej:
    print(json.dumps({k:r.get(k) for k in sorted(r.keys())}, ensure_ascii=False, default=str)[:1500])
    print("---")
