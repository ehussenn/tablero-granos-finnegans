"""Identifica contratos de COMPRA de LDC en el DW + CTGs asociados (vía traslados)."""
import os, sys, json
from pathlib import Path
import psycopg2
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OUT = ROOT / "data" / "ldc"
OUT.mkdir(parents=True, exist_ok=True)

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname=os.environ.get("FNN_DW_DB","finnegansbi"),
    connect_timeout=30,
)
cur = conn.cursor()

# 0) Listar tablas relacionadas a contratos
cur.execute("""
    SELECT table_schema, table_name FROM information_schema.tables
    WHERE table_name ILIKE '%contrato%' OR table_name ILIKE '%traslado%'
       OR table_name ILIKE '%carta_porte%' OR table_name ILIKE '%cruce%'
    ORDER BY table_name
""")
tablas = cur.fetchall()
print(f"[+] Tablas relevantes ({len(tablas)}):")
for s, t in tablas[:40]: print(f"    {s}.{t}")
print()

# 1) Mostrar columnas del resumen de contratos de compra
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'agronasajasrl_resumen_de_contrato_de_compra_de_granos'
    ORDER BY ordinal_position
""")
contrato_cols = [r[0] for r in cur.fetchall()]
print(f"[+] Cols contrato compra ({len(contrato_cols)}):")
print(f"    {contrato_cols}")
print()

# Buscar columna que contenga el nombre del cliente/cerealera/proveedor
col_proveedor = None
for c in contrato_cols:
    cl = c.lower()
    if "proveedor" in cl or "cliente" in cl or "cerealera" in cl or "comprador" in cl:
        col_proveedor = c; break
if not col_proveedor:
    print("[!] no encontre col proveedor — usando primera col que tenga 'nombre'")
    col_proveedor = next((c for c in contrato_cols if "nombre" in c.lower()), contrato_cols[1])
print(f"[+] Usando col_proveedor = {col_proveedor}")

# Listar TODOS los compradores únicos (col nombre)
cur.execute(f"""
    SELECT DISTINCT {col_proveedor}, COUNT(*) as cnt
    FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos
    GROUP BY {col_proveedor} ORDER BY cnt DESC LIMIT 50
""")
print(f"[+] Top compradores (col {col_proveedor}):")
for p, c in cur.fetchall(): print(f"    {c:5d}  {p}")

# Buscar LDC en cualquier columna de texto de la tabla
for try_col in contrato_cols:
    try:
        cur.execute(f"""
            SELECT DISTINCT {try_col}, COUNT(*) as cnt
            FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos
            WHERE UPPER({try_col}) LIKE '%LDC%' OR UPPER({try_col}) LIKE '%DREYFUS%' OR UPPER({try_col}) LIKE '%LOUIS%' OR UPPER({try_col}) LIKE '%MILDC%'
            GROUP BY {try_col} ORDER BY cnt DESC LIMIT 20
        """)
        r = cur.fetchall()
        if r:
            print(f"\n[+] LDC encontrado en col {try_col}:")
            for v, c in r: print(f"    {c:5d}  {v}")
    except Exception as e: pass

# Si encontramos en 'nombre', sigamos
cur.execute(f"""
    SELECT DISTINCT {col_proveedor}, COUNT(*) as cnt
    FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos
    WHERE UPPER({col_proveedor}) LIKE '%LDC%' OR UPPER({col_proveedor}) LIKE '%DREYFUS%' OR UPPER({col_proveedor}) LIKE '%LOUIS%' OR UPPER({col_proveedor}) LIKE '%MILDC%'
    GROUP BY {col_proveedor} ORDER BY cnt DESC
""")
proveedores_ldc = cur.fetchall()
print(f"\n[+] {len(proveedores_ldc)} variantes de LDC en col {col_proveedor}:")
for p, c in proveedores_ldc: print(f"      {c:5d}  {p}")

if not proveedores_ldc:
    print("[!] No se encontraron contratos LDC en el DW")
    sys.exit(0)

# 2) Sacar todos los nros de contrato de compra LDC
cur.execute(f"""
    SELECT *
    FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos
    WHERE UPPER({col_proveedor}) LIKE '%LDC%' OR UPPER({col_proveedor}) LIKE '%DREYFUS%' OR UPPER({col_proveedor}) LIKE '%LOUIS%'
""")
cols = [d[0] for d in cur.description]
contratos = [dict(zip(cols, row)) for row in cur.fetchall()]
print(f"\n[+] {len(contratos)} contratos de compra LDC en total")

# 3) Por cada contrato, sacar CTGs asociados desde el cruce
# Buscar columna que sea nro de contrato
col_nro = next((c for c in contrato_cols if c.lower() in ("numerodecontrato","nrocontrato","numero","nrodecontrato")), None)
if not col_nro: col_nro = next((c for c in contrato_cols if "numero" in c.lower() and "contrato" in c.lower()), contrato_cols[0])
print(f"\n[+] col_nro = {col_nro}")
nros_contrato = list({c[col_nro] for c in contratos if c.get(col_nro)})
print(f"[+] Buscando CTGs de los {len(nros_contrato)} contratos...")

# Probar estructura del cruce
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'agronasajasrl_traslado_venta_granos_carta_porte_cruce'
    ORDER BY ordinal_position
""")
cruce_cols = [r[0] for r in cur.fetchall()]
print(f"    cruce columnas: {cruce_cols[:25]}...")

# Buscar las cols relevantes
col_ctg = next((c for c in cruce_cols if "ctg" in c.lower()), None)
col_contrato = next((c for c in cruce_cols if "contrato" in c.lower() and "compra" in c.lower()), None)
if not col_contrato:
    col_contrato = next((c for c in cruce_cols if "numero" in c.lower() and "contrato" in c.lower()), None)
print(f"    col_ctg={col_ctg} | col_contrato={col_contrato}")

# Bajar todos los CTGs del cruce con sus datos
cur.execute(f"""
    SELECT * FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
    LIMIT 0
""")
cruce_cols_full = [d[0] for d in cur.description]

# Buscar CTGs cuya columna de contrato compra esté en nros_contrato
if col_contrato and nros_contrato:
    placeholders = ",".join(["%s"] * len(nros_contrato))
    cur.execute(f"""
        SELECT * FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
        WHERE {col_contrato} IN ({placeholders})
    """, nros_contrato)
    rows = cur.fetchall()
    ctgs = [dict(zip(cruce_cols_full, row)) for row in rows]
    print(f"\n[+] {len(ctgs)} CTGs encontrados en cruce para contratos LDC")

    # Convertir Decimal/date a string
    def _fix(v):
        from decimal import Decimal
        from datetime import date, datetime
        if isinstance(v, Decimal): return float(v)
        if isinstance(v, (date, datetime)): return str(v)
        return v
    ctgs_clean = [{k:_fix(v) for k,v in c.items()} for c in ctgs]
    (OUT/"ldc_ctgs.json").write_text(json.dumps(ctgs_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    -> guardado en data/ldc/ldc_ctgs.json")

    contratos_clean = [{k:_fix(v) for k,v in c.items()} for c in contratos]
    (OUT/"ldc_contratos.json").write_text(json.dumps(contratos_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    -> contratos en data/ldc/ldc_contratos.json")

    # Muestra de CTGs únicos
    ctgs_unicos = list({c.get(col_ctg) for c in ctgs if c.get(col_ctg)})
    print(f"\n[+] {len(ctgs_unicos)} CTGs únicos:")
    for ct in ctgs_unicos[:20]:
        print(f"    {ct}")
    if len(ctgs_unicos) > 20: print(f"    ... y {len(ctgs_unicos)-20} más")

cur.close()
conn.close()
