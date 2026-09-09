# -*- coding: utf-8 -*-
"""Refresh diario del cruce ARCA (CPE camiones + Liquidaciones) vs Finnegans.

Regla del usuario (09/09/2026):
  - SIEMPRE hasta la fecha ACTUAL (nunca omitir días).
  - Fuerza el tramo reciente (~45 días) sin caché para no cortarse donde quedó
    la última ventana, y re-consolida desde TODAS las ventanas (no pierde histórico).

Corre: CPE scraper (forzado tramo reciente) -> consolidar -> LPG scraper (forzado)
-> liq_kg -> cruce CTG (+--cruce) -> cruce liquidaciones -> git add/commit/push.

Pensado para la tarea programada de Windows de las 09:00 (install_arca_schedule.ps1).
Si ARCA pide captcha, el login falla y esa corrida queda registrada en el log:
hay que correrlo a mano con --visible una vez para renovar la sesión.
"""
from __future__ import annotations
import subprocess, sys, os
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCR = ROOT / "scripts"
LOG = ROOT / "data" / "arca" / "_logs" / "daily_refresh.log"
HOY = date.today().isoformat()
DESDE = (date.today() - timedelta(days=45)).isoformat()
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{date.today().isoformat()}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args: list[str], paso: str) -> bool:
    log(f"-> {paso}: {' '.join(args)}")
    try:
        r = subprocess.run([sys.executable, *args], cwd=str(ROOT), env=ENV,
                           capture_output=True, text=True, timeout=1800)
        tail = "\n".join((r.stdout or "").splitlines()[-3:])
        log(f"   {paso} rc={r.returncode} · {tail}")
        return r.returncode == 0
    except Exception as e:
        log(f"   [!] {paso} ERROR: {e}")
        return False


def main() -> int:
    log(f"===== REFRESH ARCA (hasta {HOY}, tramo forzado desde {DESDE}) =====")
    # 1) CPE camiones — tramo reciente forzado + consolidar desde todas las ventanas
    run([str(SCR / "arca_cpe_scraper.py"), "--forzar", "--desde", DESDE, "--hasta", HOY], "CPE scraper")
    run([str(SCR / "arca_cpe_scraper.py"), "--consolidar"], "CPE consolidar")
    # 2) Liquidaciones LPG — forzado + kilos
    run([str(SCR / "arca_lpg_scraper.py"), "--forzar", "--desde", DESDE, "--hasta", HOY], "LPG scraper")
    run([str(SCR / "arca_liq_kg.py")], "LPG kilos")
    # 3) Cruces
    run([str(SCR / "arca_ctg_cruce.py")], "CTG Finnegans")
    run([str(SCR / "arca_ctg_cruce.py"), "--cruce"], "CTG cruce")
    run([str(SCR / "arca_liq_cruce.py")], "LIQ cruce")
    # 4) Publicar
    try:
        subprocess.run(["git", "add", "data/arca", "data/arca_cruce.json",
                        "data/arca_liq_cruce.json", "data/ctg_finnegans.json",
                        "data/lpg_detalle.json"], cwd=str(ROOT), check=False)
        r = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=str(ROOT),
                          capture_output=True, text=True)
        if r.stdout.strip():
            msg = f"Cruce CP y Liquidaciones ARCA vs Finnegans: datos al {HOY} (auto 09:00)"
            subprocess.run(["git", "commit", "-m", msg], cwd=str(ROOT), check=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT), check=True)
            log(f"[OK] push: {msg}")
        else:
            log("[.] sin cambios en el cruce, no commit")
    except Exception as e:
        log(f"[!] git: {e}")
    log("===== FIN =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
