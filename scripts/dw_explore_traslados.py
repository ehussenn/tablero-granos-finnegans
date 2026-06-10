"""Conecta al Datawarehouse Postgres de Finnegans y explora las tablas de Traslados de Granos.
Filtra por documento 'Traslado de Granos Compra Venta' y muestra entrada+salida por CTG."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[install] psycopg2-binary...", flush=True)
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "psycopg2-binary"], check=True)
    import psycopg2
    import psycopg2.extras

import os
# Credenciales SOLO via env vars (no hardcoded en repo).
# Se levantan del .env local (gitignored) o se setean en la shell antes de correr.
DSN = dict(
    host=os.environ.get("FNN_DW_HOST") or sys.exit("falta FNN_DW_HOST"),
    dbname=os.environ.get("FNN_DW_DB", "finnegansbi"),
    user=os.environ.get("FNN_DW_USER") or sys.exit("falta FNN_DW_USER"),
    password=os.environ.get("FNN_DW_PASS") or sys.exit("falta FNN_DW_PASS"),
    port=int(os.environ.get("FNN_DW_PORT", "5432")),
    sslmode="require",
    connect_timeout=20,
)

print("[+] conectando...", flush=True)
conn = psycopg2.connect(**DSN)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
print("[+] conectado OK", flush=True)

# 1) Listar esquemas y tablas relacionadas con traslados
print("\n=== ESQUEMAS DISPONIBLES ===")
cur.execute("""SELECT schema_name FROM information_schema.schemata
               WHERE schema_name NOT IN ('pg_catalog','information_schema','public') AND schema_name NOT LIKE 'pg_%'
               ORDER BY schema_name""")
for r in cur.fetchall():
    print(f"  {r['schema_name']}")

# 2) Listar tablas con "traslad" o "ctg" o "grano" en el nombre
print("\n=== TABLAS CON 'TRASLAD' / 'CTG' / 'CARTA' / 'GRANO' ===")
cur.execute("""SELECT table_schema, table_name
               FROM information_schema.tables
               WHERE table_type='BASE TABLE'
                 AND (LOWER(table_name) LIKE '%traslad%'
                   OR LOWER(table_name) LIKE '%ctg%'
                   OR LOWER(table_name) LIKE '%carta%'
                   OR LOWER(table_name) LIKE '%grano%')
               ORDER BY table_schema, table_name LIMIT 40""")
tablas_grano = cur.fetchall()
for r in tablas_grano:
    print(f"  {r['table_schema']}.{r['table_name']}")

# 3) Listar también vistas (a veces los datos finos están en views)
print("\n=== VIEWS CON 'TRASLAD' / 'CTG' / 'GRANO' ===")
cur.execute("""SELECT table_schema, table_name
               FROM information_schema.views
               WHERE LOWER(table_name) LIKE '%traslad%'
                  OR LOWER(table_name) LIKE '%ctg%'
                  OR LOWER(table_name) LIKE '%carta%'
                  OR LOWER(table_name) LIKE '%grano%'
               ORDER BY table_schema, table_name LIMIT 40""")
for r in cur.fetchall():
    print(f"  V: {r['table_schema']}.{r['table_name']}")

conn.close()
print("\n[+] OK")
