"""Busca CTGs que empiecen con 1013227 (el número del screenshot estaba cortado)."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# CTGs que matchean el prefijo y que tengan ambos lados
cur.execute("""SELECT numerodocumentoadicional, operaciontipo, transaccionsubtiponombre,
                       organizacionnombre, nombrecontrato, pesoneto, numerodocumento, fecha
                FROM public.agronasajasrl_traslado_de_granos
                WHERE numerodocumentoadicional LIKE '10132275%'
                ORDER BY numerodocumentoadicional, operaciontipo""")
rows = cur.fetchall()
print(f"CTGs con prefijo 10132275: {len(set(r['numerodocumentoadicional'] for r in rows))}")
prev = None
for r in rows:
    ctg = r['numerodocumentoadicional']
    if ctg != prev:
        print(f"\n>>> CTG {ctg}:")
        prev = ctg
    print(f"  {r['operaciontipo']:<7} | {r['transaccionsubtiponombre']:<40} | CP={r['numerodocumento']:<20} | org={r['organizacionnombre']:<40} | cto={r['nombrecontrato']:<25} | kg={r['pesoneto']}")

conn.close()
