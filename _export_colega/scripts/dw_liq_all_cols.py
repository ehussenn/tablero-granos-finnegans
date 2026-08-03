"""Lista TODAS las columnas de liquidacion_venta_granos y muestra una fila con todo."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

t = "agronasajasrl_liquidacion_venta_granos"
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (t,))
print(f"COLUMNAS de {t}:")
for r in cur.fetchall(): print(f"  • {r['column_name']:<40} {r['data_type']}")

cur.execute(f"SELECT * FROM public.{t} WHERE numerocoe != '' ORDER BY fecha DESC LIMIT 3")
print(f"\n3 filas con numerocoe (TODOS los campos):")
for i, r in enumerate(cur.fetchall()):
    print(f"\n--- Fila {i+1} ---")
    for k, v in r.items():
        print(f"  {k:<40} = {repr(v)[:90]}")

# También para liquidacionventagranos (la chica con 23 cols)
t2 = "agronasajasrl_liquidacionventagranos"
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (t2,))
cols2 = [r["column_name"] for r in cur.fetchall()]
print(f"\n\nCOLUMNAS de {t2}: {cols2}")
cur.execute(f"SELECT * FROM public.{t2} LIMIT 2")
for i, r in enumerate(cur.fetchall()):
    print(f"\n--- {t2} Fila {i+1} ---")
    for k, v in r.items():
        if v not in (None, "", "0", "0.0", "0.00", "0.0000"):
            print(f"  {k:<40} = {repr(v)[:90]}")

conn.close()
