"""Copias de seguridad cifradas de una base de PostgreSQL, y su restauración (cambio 7c).

    python -m modelo_ia.despliegue.respaldo_pg crear      --frase-archivo secretos/respaldo_frase
    python -m modelo_ia.despliegue.respaldo_pg verificar  ARCHIVO --frase-archivo ...   # descifra, comprueba que es una copia válida y lista sus tablas
    python -m modelo_ia.despliegue.respaldo_pg restaurar  ARCHIVO [--forzar] --frase-archivo ...
    python -m modelo_ia.despliegue.respaldo_pg rotar      [--simular]
    python -m modelo_ia.despliegue.respaldo_pg ciclo      --cada-horas 6

Es lo mismo que `respaldo.py` (SQLite) en lo que importa: el mismo formato de archivo (.afbk: AES-256 con la clave derivada de la frase, HMAC aparte, un archivo
alterado o con otra frase se rechaza ANTES de descifrar), la misma rotación y la misma copia afuera. Solo cambia cómo se saca la copia: `pg_dump` (formato
«custom», consistente aunque el servicio esté escribiendo) y cómo se restaura: `pg_restore` en UNA transacción.

La dirección sale de MODELO_IA_BD_URL (o MODELO_IA_BD_URL_ARCHIVO): la contraseña NO va en la línea de comandos de pg_dump (se pasa por la variable de entorno
PGPASSWORD solo a ese proceso), para que no aparezca en la lista de procesos. Hacen falta `pg_dump` y `pg_restore` (paquete postgresql-client, de la misma versión
mayor que el servidor o más nueva); con PG_DUMP y PG_RESTORE se elige otro ejecutable.
"""

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .respaldo import (
    ErrorDeRespaldo,
    ITERACIONES,
    MAGIA,
    _abrir,
    _anotar_estado,
    _cifrar,
    _escribir_privado,
    _llave_mac,
    copiar_afuera,
    leer_frase,
    rotar,
)

import hashlib
import hmac

log = logging.getLogger("antifraude.respaldo_pg")

TABLAS_ESPERADAS = (
    "comercios",
    "acuerdos",
    "claves",
    "evaluaciones",
    "evaluacion_reglas",
    "revisiones",
    "resultados",
    "lista_negra",
    "auditoria",
    "modelos_registro",
    "migraciones",
)
LIMITE_SEGUNDOS = 3600
FIRMA_PG = b"PGDMP"  # así empieza todo archivo de pg_dump en formato custom
QUE_PG = "una copia de PostgreSQL (¿es una copia de SQLite? usa respaldo.py)"


def _direccion() -> str:
    e = os.environ.get
    if e("MODELO_IA_BD_URL_ARCHIVO"):
        try:
            return Path(e("MODELO_IA_BD_URL_ARCHIVO", "")).read_text(encoding="utf-8").strip()
        except OSError:
            raise ErrorDeRespaldo(
                "No se puede leer el archivo con la dirección de PostgreSQL (MODELO_IA_BD_URL_ARCHIVO)."
            ) from None
    if e("MODELO_IA_BD_URL"):
        return e("MODELO_IA_BD_URL", "").strip()
    raise ErrorDeRespaldo("Define MODELO_IA_BD_URL (o MODELO_IA_BD_URL_ARCHIVO) con la dirección de PostgreSQL.")


def _partes(url: str) -> Tuple[List[str], Dict[str, str], Optional[str]]:
    """Los argumentos de conexión SIN contraseña, las variables de entorno que la llevan, y el esquema (si la dirección fija uno: se respalda solo ese)."""
    p = urlsplit(url)
    if p.scheme not in ("postgresql", "postgres") or not p.path.strip("/"):
        raise ErrorDeRespaldo("La dirección de PostgreSQL no es válida (postgresql://usuario:clave@host:5432/base).")
    consulta = parse_qs(p.query)
    args = ["-h", p.hostname or "localhost", "-p", str(p.port or 5432), "-d", unquote(p.path.strip("/"))]
    if p.username:
        args += ["-U", unquote(p.username)]
    entorno: Dict[str, str] = {}
    if p.password:
        entorno["PGPASSWORD"] = unquote(p.password)
    if consulta.get("sslmode"):
        entorno["PGSSLMODE"] = consulta["sslmode"][-1]
    esquema = None
    m = re.search(r"search_path[=\s]+([a-z_][a-z0-9_]*)", " ".join(consulta.get("options", [])))
    if m:
        esquema = m.group(1)
    return args, entorno, esquema


def _herramienta(nombre: str, variable: str) -> str:
    ruta = os.environ.get(variable) or shutil.which(nombre)
    if not ruta:
        raise ErrorDeRespaldo(
            f"No encuentro «{nombre}». Instala postgresql-client (de la misma versión mayor que el servidor o más nueva) o indica el ejecutable en {variable}."
        )
    return ruta


def _ejecutar(comando: List[str], entorno_extra: Dict[str, str], que: str) -> subprocess.CompletedProcess:
    entorno = {**os.environ, **entorno_extra}
    try:
        r = subprocess.run(comando, env=entorno, capture_output=True, timeout=LIMITE_SEGUNDOS)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise ErrorDeRespaldo(f"{que} no terminó ({type(e).__name__}).") from None
    if r.returncode != 0:
        detalle = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["sin detalle"]
        raise ErrorDeRespaldo(f"{que} falló: {detalle[0][:200]}")
    return r


def _tablas_de(copia: Path) -> List[str]:
    """Las tablas con datos que trae la copia, según `pg_restore --list` (también comprueba que el archivo es una copia válida de PostgreSQL)."""
    r = _ejecutar([_herramienta("pg_restore", "PG_RESTORE"), "--list", str(copia)], {}, "pg_restore --list")
    tablas = re.findall(r"TABLE DATA (?:\S+) (\S+) ", r.stdout.decode("utf-8", "replace"))
    return sorted(set(tablas))


def crear(
    carpeta: Path, frase_archivo: Path, url: Optional[str] = None, ahora: Optional[datetime] = None
) -> Dict[str, Any]:
    url = url or _direccion()
    carpeta, frase_archivo = Path(carpeta), Path(frase_archivo)
    frase = leer_frase(frase_archivo)
    args, entorno, esquema = _partes(url)
    carpeta.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(carpeta, 0o700)
    except OSError:
        pass
    temporal = Path(tempfile.mkdtemp(prefix="afbkpg_", dir=carpeta))
    try:
        os.chmod(temporal, 0o700)
        copia = temporal / "copia.pgdump"
        comando = [_herramienta("pg_dump", "PG_DUMP"), *args, "--format=custom", "--no-owner", "--file", str(copia)]
        if esquema:
            comando += ["--schema", esquema]
        _ejecutar(comando, entorno, "pg_dump")
        tablas = _tablas_de(copia)  # si la copia está dañada o no trae las tablas, aquí se detecta y NO se guarda
        faltan = [t for t in TABLAS_ESPERADAS if t not in tablas]
        if faltan:
            raise ErrorDeRespaldo(f"La copia no trae las tablas {faltan}: no se guarda. ¿La base está vacía o es otra?")
        sal = os.urandom(16)
        cifrado = _cifrar(copia, frase_archivo, ITERACIONES)
        cabecera = MAGIA + ITERACIONES.to_bytes(4, "big") + sal
        mac = hmac.new(_llave_mac(frase, sal, ITERACIONES), cabecera + cifrado, hashlib.sha256).digest()
        momento = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
        final = carpeta / momento.strftime("antifraude_%Y%m%d_%H%M%S.afbk")
        _escribir_privado(final, cabecera + mac + cifrado)
        return {"archivo": final, "bytes": final.stat().st_size, "tablas": tablas, "creado": momento.isoformat()}
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def verificar(archivo: Path, frase_archivo: Path) -> List[str]:
    plano = _abrir(Path(archivo), Path(frase_archivo), FIRMA_PG, QUE_PG)
    temporal = Path(tempfile.mkdtemp(prefix="afbkpg_v_"))
    try:
        os.chmod(temporal, 0o700)
        copia = temporal / "v.pgdump"
        _escribir_privado(copia, plano)
        tablas = _tablas_de(copia)
        faltan = [t for t in TABLAS_ESPERADAS if t not in tablas]
        if faltan:
            raise ErrorDeRespaldo(f"La copia descifra bien pero no trae las tablas {faltan}.")
        return tablas
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def restaurar(archivo: Path, frase_archivo: Path, url: Optional[str] = None, forzar: bool = False) -> Dict[str, Any]:
    """Restaura en UNA transacción (si algo falla no queda nada a medias). Sin `forzar` no pisa objetos que ya existan; con `forzar` los reemplaza."""
    url = url or _direccion()
    tablas = verificar(archivo, frase_archivo)  # primero se prueba que la copia sirve
    plano = _abrir(Path(archivo), Path(frase_archivo), FIRMA_PG, QUE_PG)
    args, entorno, _ = _partes(url)
    temporal = Path(tempfile.mkdtemp(prefix="afbkpg_r_"))
    try:
        os.chmod(temporal, 0o700)
        copia = temporal / "r.pgdump"
        _escribir_privado(copia, plano)
        comando = [
            _herramienta("pg_restore", "PG_RESTORE"),
            *args,
            "--no-owner",
            "--exit-on-error",
            "--single-transaction",
        ]
        if forzar:
            comando += ["--clean", "--if-exists"]
        _ejecutar(comando + [str(copia)], entorno, "pg_restore")
    finally:
        shutil.rmtree(temporal, ignore_errors=True)
    return {"tablas": tablas}


def una_vuelta(carpeta: Path, frase_archivo: Path, diarios: int, semanales: int, mensuales: int) -> Dict[str, Any]:
    try:
        r = crear(carpeta, frase_archivo)
        rotar(carpeta, diarios, semanales, mensuales)
        copiar_afuera(r["archivo"])
        _anotar_estado(carpeta, True, r["archivo"], None)
        return r
    except ErrorDeRespaldo as e:
        _anotar_estado(carpeta, False, None, str(e))
        raise


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    e = os.environ.get
    ap = argparse.ArgumentParser(
        prog="python -m modelo_ia.despliegue.respaldo_pg", description="Copias de seguridad cifradas de PostgreSQL."
    )
    ap.add_argument("--carpeta", type=Path, default=Path(e("MODELO_IA_RESPALDOS", "/respaldos")))
    ap.add_argument(
        "--frase-archivo", type=Path, default=Path(e("MODELO_IA_RESPALDO_FRASE_ARCHIVO", "/run/secrets/respaldo_frase"))
    )
    ap.add_argument("--diarios", type=int, default=7)
    ap.add_argument("--semanales", type=int, default=4)
    ap.add_argument("--mensuales", type=int, default=3)
    sub = ap.add_subparsers(dest="orden", required=True)
    sub.add_parser("crear")
    v = sub.add_parser("verificar")
    v.add_argument("archivo", type=Path)
    r = sub.add_parser("restaurar")
    r.add_argument("archivo", type=Path)
    r.add_argument("--forzar", action="store_true")
    ro = sub.add_parser("rotar")
    ro.add_argument("--simular", action="store_true")
    c = sub.add_parser("ciclo")
    c.add_argument("--cada-horas", type=float, default=6.0)
    a = ap.parse_args(argv)
    try:
        if a.orden == "crear":
            x = una_vuelta(a.carpeta, a.frase_archivo, a.diarios, a.semanales, a.mensuales)
            print(f"Copia creada: {x['archivo'].name} ({x['bytes']:,} bytes)".replace(",", "."))
            print("Tablas en la copia:", ", ".join(x["tablas"]))
        elif a.orden == "verificar":
            print("Copia VÁLIDA. Tablas en la copia:", ", ".join(verificar(a.archivo, a.frase_archivo)))
        elif a.orden == "restaurar":
            x = restaurar(a.archivo, a.frase_archivo, forzar=a.forzar)
            print("Restaurada en la base de MODELO_IA_BD_URL. Tablas:", ", ".join(x["tablas"]))
        elif a.orden == "rotar":
            x = rotar(a.carpeta, a.diarios, a.semanales, a.mensuales, a.simular)
            print(
                ("Se borrarían" if a.simular else "Borradas")
                + f": {len(x['borradas'])} | conservadas: {len(x['conservadas'])}"
            )
        elif a.orden == "ciclo":
            while True:
                try:
                    x = una_vuelta(a.carpeta, a.frase_archivo, a.diarios, a.semanales, a.mensuales)
                    log.info("Copia creada: %s", x["archivo"].name)
                except ErrorDeRespaldo as err:
                    log.error("Falló la copia: %s", err)  # se reintenta en la próxima vuelta; el estado queda anotado
                time.sleep(max(60.0, a.cada_horas * 3600))
        return 0
    except ErrorDeRespaldo as err:
        print("ERROR:", err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
