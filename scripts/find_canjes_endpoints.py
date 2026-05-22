"""Buscar endpoints API para Canjes:
 1. Precios pizarra / dispo / Bolsa Rosario
 2. Clientes-Vendedores (USR_ClientesVendedores)
"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
spec = json.load(open(r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts\finnegans_swagger.json", encoding="utf-8"))

needles = ["precio", "pizarra", "dispo", "bolsa", "rosario", "BCR", "BCBA", "cotizacion", "tipo de cambio",
           "lista de precios", "lista precios", "ClientesVendedor", "USR_Clientes", "VendedorCliente"]

print(f"=== Tags que matchean alguno de: {needles} ===\n")
for t in spec.get("tags", []):
    name = (t.get("name") or "") + " " + (t.get("description") or "")
    nl = name.lower()
    if any(n.lower() in nl for n in needles):
        print(f"  [{t.get('name')}]  {t.get('description','')[:120]}")

print(f"\n=== Paths que matchean ===\n")
for path, ops in spec.get("paths", {}).items():
    if not isinstance(ops, dict): continue
    pl = path.lower()
    matched = any(n.lower() in pl for n in needles)
    if matched:
        for method, op in ops.items():
            if not isinstance(op, dict): continue
            tags = op.get("tags", [])
            print(f"  [{method.upper():<5}] {path:<70}  tags={tags}")
