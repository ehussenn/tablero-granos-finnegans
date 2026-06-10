"""Inspecciona la tabla de cruce y la de traslado_de_granos."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2
import psycopg2.extras

DSN = dict(
    host=os.environ.get("FNN_DW_HOST") or sys.exit("falta FNN_DW_HOST"),
    dbname=os.environ.get("FNN_DW_DB", "finnegansbi"),
    user=os.environ.get("FNN_DW_USER") or sys.exit("falta FNN_DW_USER"),
    password=os.environ.get("FNN_DW_PASS") or sys.exit("falta FNN_DW_PASS"),
    port=int(os.environ.get("FNN_DW_PORT", "5432")),
    sslmode="require",
    connect_timeout=20,
)
conn = psycopg2.connect(**DSN)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

for table in [
    "agronasajasrl_traslado_venta_granos_carta_porte_cruce",
    "agronasajasrl_traslado_de_granos",
]:
    print("="*100)
    print(f"TABLA: {table}")
    print("="*100)
    cur.execute(f"""SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = %s ORDER BY ordinal_position""", (table,))
    cols = cur.fetchall()
    print(f"  {len(cols)} columnas:")
    for c in cols:
        print(f"    {c['column_name']:<50} {c['data_type']}")

    cur.execute(f"SELECT COUNT(*) as c FROM public.{table}")
    print(f"  Total filas: {cur.fetchone()['c']}")

    print(f"\n  PRIMERAS 3 FILAS:")
    cur.execute(f"SELECT * FROM public.{table} LIMIT 3")
    for i, r in enumerate(cur.fetchall()):
        print(f"\n  --- Fila {i+1} ---")
        for k, v in r.items():
            print(f"    {k:<50} = {repr(v)[:80]}")
    print()

conn.close()
