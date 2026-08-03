"""Explorar endpoints de Traslados Granos para encontrar el que tenga CTG + contratos."""
import sys, json
sys.path.insert(0, r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts")
import finnegans_api as api
sys.stdout.reconfigure(encoding="utf-8")

spec = json.load(open(r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts\finnegans_swagger.json", encoding="utf-8"))

# Listar todos los endpoints con "traslado" en el path o tag
print("=== Endpoints con TRASLADO ===\n")
candidates = []
for path, ops in spec.get("paths", {}).items():
    if isinstance(ops, dict):
        for method, op in ops.items():
            if isinstance(op, dict):
                tags = op.get("tags") or []
                t = " ".join(tags).lower()
                p = path.lower()
                if "traslad" in t or "traslad" in p or "ctg" in p or "ctg" in t:
                    candidates.append((method.upper(), path, tags, op))

for m, p, tags, op in candidates:
    print(f"[{m}] {p}  tags={tags}")
    for param in op.get("parameters", []) or []:
        req = "✓" if param.get("required") else " "
        desc = (param.get("description") or "")[:70]
        nm = param.get('name') or '?'
        print(f"   {req} {nm:<35} {desc}")
    print()

# Probar /reports/trasladoGranos (rango fechas)
print("\n" + "="*70)
print("Probando /reports/trasladoGranos con rango 2026")
print("="*70)
try:
    d = api.call("/reports/trasladoGranos", {
        "PARAMFechaDesde": "2026-01-01",
        "PARAMFechaHasta": "2026-12-31",
    })
    print(f"Filas: {len(d) if isinstance(d,list) else 'no list'}")
    if isinstance(d, list) and d:
        print(f"Columnas ({len(d[0])}): {list(d[0].keys())}")
        print(f"\nSample row (primero):")
        for k, v in d[0].items():
            print(f"  {k:<45} = {repr(v)[:80]}")
        # buscar contratos asociados
        print(f"\nCampos con 'contrato', 'ctg', 'cliente', 'comprador':")
        for k in d[0].keys():
            kl = k.lower()
            if "contrato" in kl or "ctg" in kl or "cliente" in kl or "comprador" in kl or "vinculac" in kl:
                vals = [r.get(k) for r in d[:5]]
                print(f"  {k}: ejemplos {vals}")
except Exception as e:
    print(f"ERROR: {str(e)[:300]}")

# Si trae CTGs, probar con uno
print("\n" + "="*70)
print("Probando /reports/AGRI_TRASLADOS")
print("="*70)
try:
    d = api.call("/reports/AGRI_TRASLADOS", {})
    print(f"Filas: {len(d) if isinstance(d,list) else 'no list'}")
    if isinstance(d, list) and d:
        print(f"Columnas ({len(d[0])}): {list(d[0].keys())[:30]}")
except Exception as e:
    print(f"ERROR: {str(e)[:200]}")
