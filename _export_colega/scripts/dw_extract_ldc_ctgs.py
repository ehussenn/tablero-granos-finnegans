"""Extrae TODOS los CTGs con destino LDC desde el cruce + enriquecimiento de traslados."""
import os, sys, json
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
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

def fix(v):
    if isinstance(v, Decimal): return float(v)
    if isinstance(v, (date, datetime)): return str(v)
    return v

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname=os.environ.get("FNN_DW_DB","finnegansbi"),
    connect_timeout=30,
)
cur = conn.cursor()

# Sacar todos los CTGs LDC desde 01/01/2026
cur.execute("""
    SELECT * FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
    WHERE (UPPER(destino) LIKE '%LDC%' OR UPPER(destino) LIKE '%DREYFUS%' OR UPPER(organizacionnombre) LIKE '%LDC%')
      AND (fecha >= '2026-01-01' OR fechadescarga >= '2026-01-01' OR fechaarribo >= '2026-01-01')
""")
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
ctgs = [{c:fix(v) for c,v in zip(cols, row)} for row in rows]
print(f"[+] {len(ctgs)} traslados LDC desde 01/01/2026")

# Guardar
(OUT/"ldc_ctgs.json").write_text(json.dumps(ctgs, ensure_ascii=False, indent=2), encoding="utf-8")

# Mostrar columnas con CTG
print(f"\n[+] Sample primer registro:")
if ctgs:
    for k,v in ctgs[0].items():
        if v is not None: print(f"    {k} = {v}")

# Enriquecer con datos de traslado_de_granos (para sacar el CTG real)
print(f"\n[+] Enriqueciendo con agronasajasrl_traslado_de_granos...")
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'agronasajasrl_traslado_de_granos'
    ORDER BY ordinal_position
""")
tg_cols = [r[0] for r in cur.fetchall()]
print(f"    cols ({len(tg_cols)}): {tg_cols}")

# Si hay col que sea CTG en cruce, listarlas
ctg_field = None
for c in cols:
    if 'ctg' in c.lower():
        ctg_field = c; break
print(f"\n[+] Field CTG en cruce: {ctg_field}")

# Listar valores únicos de numerodocumento (es el CTG?)
nums_unicos = list({c.get("numerodocumento") for c in ctgs if c.get("numerodocumento")})
print(f"[+] {len(nums_unicos)} valores únicos en col 'numerodocumento'")
print(f"    sample: {nums_unicos[:5]}")

doc_unicos = list({c.get("documento") for c in ctgs if c.get("documento")})
print(f"[+] {len(doc_unicos)} valores únicos en col 'documento'")
print(f"    sample: {doc_unicos[:5]}")

# Sacar CTGs únicos buscando en traslado_de_granos
if doc_unicos:
    placeholders = ",".join(["%s"] * len(doc_unicos[:100]))
    cur.execute(f"""
        SELECT * FROM agronasajasrl_traslado_de_granos
        WHERE numerodocumento IN ({placeholders})
        LIMIT 5
    """, doc_unicos[:100])
    cols2 = [d[0] for d in cur.description]
    rows2 = cur.fetchall()
    if rows2:
        print(f"\n[+] Match en traslado_de_granos:")
        for k,v in zip(cols2, rows2[0]):
            if v is not None: print(f"    {k} = {v}")

cur.close(); conn.close()
