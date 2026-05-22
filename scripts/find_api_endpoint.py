"""Busca paths exactos y descripciones de las APIs que nos interesan."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

with open(r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts\finnegans_swagger.json", encoding="utf-8") as fh:
    spec = json.load(fh)

WANT_TAGS = {
    "resumenContratosVentaGranos",
    "ResumenContratoCompraGranos",
    "APILiquidacionVentaGranos",
    "USR_RESSTOCKDEP",
    "USR_ComposicionSaldosResumenParaEmail_API",
    "USR_TRASGRANCOMPVENT_API",
}

for path, ops in spec.get("paths", {}).items():
    if not isinstance(ops, dict): continue
    for method, op in ops.items():
        if not isinstance(op, dict): continue
        tags = op.get("tags", []) or []
        if any(t in WANT_TAGS for t in tags):
            print(f"\n[{method.upper()}] {path}")
            print(f"   tag: {tags}")
            print(f"   summary: {op.get('summary','')[:200]}")
            print(f"   params:")
            for p in op.get("parameters", []) or []:
                print(f"     - {p.get('name')}  in={p.get('in')}  type={p.get('type','?')}  required={p.get('required',False)}  desc={(p.get('description') or '')[:80]}")
            secs = op.get("security") or []
            if secs:
                print(f"   security: {secs}")

# Ademas: buscar como se autoriza globalmente
print("\n\n=== securityDefinitions ===")
print(json.dumps(spec.get("securityDefinitions", {}), indent=2))
print("\n=== security (global) ===")
print(json.dumps(spec.get("security", {}), indent=2))
