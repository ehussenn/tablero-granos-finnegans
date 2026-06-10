"""Inspección profunda del CRUCE table — entender si tiene ambos lados (compra+venta) o solo uno."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
T = "agronasajasrl_traslado_venta_granos_carta_porte_cruce"

# Total filas + cuántas con CTG
cur.execute(f"SELECT COUNT(*) tot, COUNT(NULLIF(numerodocumentoadicional,'')) con_ctg FROM public.{T}")
r = cur.fetchone()
print(f"Total filas: {r['tot']} | con CTG: {r['con_ctg']}")

# Subtipos
print("\n=== SUBTIPOS ===")
cur.execute(f"SELECT transaccionsubtiponombre, COUNT(*) c FROM public.{T} GROUP BY 1 ORDER BY c DESC")
for r in cur.fetchall():
    print(f"  {r['c']:>6}  {r['transaccionsubtiponombre']!r}")

# Para 5 CTGs random con CTG completo, ver cuántas filas tiene cada uno
print("\n=== 5 CTGs aleatorios: cuántas filas por CTG ===")
cur.execute(f"""SELECT numerodocumentoadicional, COUNT(*) c
                FROM public.{T} WHERE numerodocumentoadicional != ''
                GROUP BY 1 HAVING COUNT(*) >= 1 ORDER BY c DESC, numerodocumentoadicional LIMIT 5""")
for r in cur.fetchall():
    ctg = r['numerodocumentoadicional']
    print(f"\n  CTG {ctg}: {r['c']} filas")
    cur.execute(f"""SELECT documento, transaccionsubtiponombre, organizacionnombre, nombrecontrato, pesoneto, fecha
                    FROM public.{T} WHERE numerodocumentoadicional=%s""", (ctg,))
    for s in cur.fetchall():
        print(f"    doc={s['documento']!r:40s} | sub={s['transaccionsubtiponombre']!r:50s}")
        print(f"      org={s['organizacionnombre']!r:35s} | cto={s['nombrecontrato']!r} | kg={s['pesoneto']} | f={s['fecha']}")

conn.close()
