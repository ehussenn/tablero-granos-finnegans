"""Escanea PAYLOAD.pilot (venta) y PAYLOAD.compra buscando data sucia."""
import json, re, sys
from pathlib import Path
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "index.html").read_text(encoding="utf-8")
m = re.search(r'const PAYLOAD\s*=\s*(\{.*?\});\s*\n', html, re.S)
if not m:
    print("No encontre PAYLOAD"); sys.exit(1)
P = json.loads(m.group(1))
pilot = P.get("pilot") or []     # venta
compra = P.get("compra") or []
print(f"Venta (pilot): {len(pilot)} contratos · Compra: {len(compra)} contratos\n")

def scan(rows, label):
    print(f"\n{'='*60}\n== {label.upper()} ({len(rows)} filas)\n{'='*60}")

    # 1) Anulados / estados raros
    estados = Counter()
    for r in rows:
        e = r.get("estado") or r.get("estadocontrato") or r.get("estado_contrato")
        if e: estados[e] += 1
    print(f"\n[1] Estados distintos:")
    for e, n in estados.most_common(): print(f"      {n:>5}  '{e}'")

    # 2) Campañas
    camps = Counter(r.get("campana") or "(vacío)" for r in rows)
    print(f"\n[2] Campañas distintas:")
    for c, n in sorted(camps.items()): print(f"      {n:>5}  '{c}'")

    # 3) Cantidad ajustada = 0 o negativa
    zero = [r for r in rows if not (Number := r.get("cantidadmax") or r.get("cantidadajustada") or 0)]
    neg = [r for r in rows if (r.get("cantidadmax") or r.get("cantidadajustada") or 0) < 0]
    print(f"\n[3] Contratos con cantidad ajustada = 0: {len(zero)}")
    print(f"    Contratos con cantidad ajustada NEGATIVA: {len(neg)}")
    if neg[:3]:
        for r in neg[:3]:
            print(f"      ej: {r.get('contrato','?')} {r.get('producto','?')} cant={r.get('cantidadmax') or r.get('cantidadajustada')}")

    # 4) Organizaciones (proveedor/comprador) con espacios o casing raro
    orgs = Counter(r.get("organizacion") or "(vacío)" for r in rows)
    raras = []
    norm_groups = {}
    for o in orgs:
        clean = re.sub(r'\s+', ' ', o).strip().upper().replace('.','').replace(',','')
        norm_groups.setdefault(clean, []).append(o)
    duplicados_por_norm = {k: v for k, v in norm_groups.items() if len(v) > 1}
    print(f"\n[4] Total organizaciones únicas: {len(orgs)}")
    print(f"    Posibles DUPLICADOS por nombre (mismo valor normalizado, distinto en data):")
    for k, vs in list(duplicados_por_norm.items())[:10]:
        print(f"      → {vs}")

    # 5) Productos vacíos o raros
    prods = Counter(r.get("producto") or "(vacío)" for r in rows)
    vacios = prods.get("(vacío)", 0)
    print(f"\n[5] Productos: {len(prods)} únicos · {vacios} contratos SIN producto")
    # top 5 productos por contratos
    print(f"    Top 5 productos por #contratos:")
    for p, n in prods.most_common(5):
        print(f"      {n:>5}  '{p}'")

    # 6) Filas con cliente/proveedor vacío
    sin_org = sum(1 for r in rows if not (r.get("organizacion") or "").strip())
    print(f"\n[6] Contratos SIN proveedor/comprador: {sin_org}")

scan(pilot, "VENTA")
scan(compra, "COMPRA")
