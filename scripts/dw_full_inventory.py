"""Lista TODAS las tablas del DW de Agronasaja con su row count y columnas relevantes.
Solo SELECT — read-only."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Todas las tablas del esquema public que arrancan con agronasajasrl_
cur.execute("""SELECT table_name FROM information_schema.tables
               WHERE table_schema='public' AND table_type='BASE TABLE'
                 AND table_name LIKE 'agronasajasrl_%'
               ORDER BY table_name""")
tables = [r["table_name"] for r in cur.fetchall()]
print(f"=== {len(tables)} TABLAS agronasajasrl_* ===\n")

# Para cada tabla: count + primeras 3 columnas
for t in tables:
    try:
        cur.execute(f"SELECT COUNT(*) c FROM public.{t}")
        c = cur.fetchone()["c"]
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name=%s ORDER BY ordinal_position LIMIT 8""", (t,))
        cols = [r["column_name"] for r in cur.fetchall()]
        print(f"  {c:>7} filas  {t}")
        print(f"            cols: {', '.join(cols)}")
    except Exception as e:
        print(f"  ERROR     {t}: {str(e)[:60]}")

conn.close()
