"""Copias de seguridad cifradas de la base de datos, y su restauración.

    python -m modelo_ia.despliegue.respaldo crear      --frase-archivo secretos/respaldo_frase
    python -m modelo_ia.despliegue.respaldo verificar  ARCHIVO --frase-archivo ...        # descifra, comprueba integridad y cuenta filas
    python -m modelo_ia.despliegue.respaldo restaurar  ARCHIVO --destino BD --frase-archivo ... [--forzar]
    python -m modelo_ia.despliegue.respaldo rotar      [--simular]
    python -m modelo_ia.despliegue.respaldo ciclo      --cada-horas 6                     # lo que corre dentro del contenedor «respaldo»

Cómo se protege la copia:
  - Se toma con la API de copia de SQLite (consistente aunque el servicio esté escribiendo) y se comprueba su integridad ANTES de cifrarla.
  - Se cifra con AES-256 (openssl, clave derivada de la frase con PBKDF2 y 600.000 vueltas) y se autentica con un HMAC-SHA256 con una clave distinta
    (cifrar y luego autenticar): un archivo alterado, truncado o con otra frase se rechaza ANTES de descifrar.
  - La frase se lee de un archivo (nunca de la línea de comandos, para que no aparezca en la lista de procesos) y el archivo debe ser solo tuyo (chmod 600).
  - Los archivos quedan con permisos 600 y se escriben de forma atómica.
Una copia en el mismo servidor NO protege si el servidor se pierde: usa AFBK_COMANDO_DESPUES para copiarla a otro lugar (ver LEEME.md).
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("antifraude.respaldo")

MAGIA = b"AFBK1\n"
ITERACIONES = 600_000
ITER_MIN, ITER_MAX = 1_000, 5_000_000
FRASE_LARGO_MINIMO = 20
PATRON = re.compile(r"^antifraude_(\d{8})_(\d{6})\.afbk$")
ESTADO = "ultimo_respaldo.json"


class ErrorDeRespaldo(Exception):
    pass


# ------------------------------------------------------------------ la frase
def leer_frase(ruta) -> bytes:
    ruta = Path(ruta)
    try:
        st = ruta.stat()
        crudo = ruta.read_bytes()
    except OSError:
        raise ErrorDeRespaldo(f"No se puede leer el archivo de la frase: {ruta.name}")
    en_secretos_de_docker = str(ruta).startswith("/run/secrets/")           # ahí Docker los monta con los permisos del archivo del servidor y los aísla por contenedor
    if os.name == "posix" and not en_secretos_de_docker and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ErrorDeRespaldo(f"El archivo de la frase ({ruta.name}) lo pueden leer otras personas. Cierra sus permisos:  chmod 600 {ruta}")
    frase = crudo.strip()
    if b"\n" in frase or b"\r" in frase:
        raise ErrorDeRespaldo("La frase debe tener una sola línea.")
    if len(frase) < FRASE_LARGO_MINIMO:
        raise ErrorDeRespaldo(f"La frase es demasiado corta (mínimo {FRASE_LARGO_MINIMO} caracteres).")
    return frase


def _openssl() -> str:
    ruta = shutil.which("openssl")
    if not ruta:
        raise ErrorDeRespaldo("No encuentro «openssl» en este sistema.")
    return ruta


def _llave_mac(frase: bytes, sal: bytes, iteraciones: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", b"mac|" + frase, sal, iteraciones, dklen=32)


def _cifrar(datos_ruta: Path, frase_ruta: Path, iteraciones: int) -> bytes:
    r = subprocess.run([_openssl(), "enc", "-aes-256-cbc", "-pbkdf2", "-iter", str(iteraciones), "-md", "sha256", "-salt", "-pass", f"file:{frase_ruta}", "-in", str(datos_ruta)],
                       capture_output=True)
    if r.returncode != 0:
        raise ErrorDeRespaldo("openssl no pudo cifrar la copia.")
    return r.stdout


def _descifrar(cifrado: bytes, frase_ruta: Path, iteraciones: int) -> bytes:
    r = subprocess.run([_openssl(), "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", str(iteraciones), "-md", "sha256", "-pass", f"file:{frase_ruta}"], input=cifrado, capture_output=True)
    if r.returncode != 0:
        raise ErrorDeRespaldo("No se pudo descifrar (frase equivocada o archivo dañado).")
    return r.stdout


def _abrir(archivo: Path, frase_ruta: Path) -> bytes:
    """Comprueba la autenticidad y devuelve la base de datos en claro (bytes). Rechaza todo archivo alterado antes de descifrar."""
    frase = leer_frase(frase_ruta)
    try:
        datos = Path(archivo).read_bytes()
    except OSError:
        raise ErrorDeRespaldo(f"No se puede leer la copia: {Path(archivo).name}")
    if len(datos) < len(MAGIA) + 4 + 16 + 32 + 16 or not datos.startswith(MAGIA):
        raise ErrorDeRespaldo("El archivo no es una copia de seguridad válida (o está truncado).")
    i = len(MAGIA)
    iteraciones = int.from_bytes(datos[i:i + 4], "big")
    if not ITER_MIN <= iteraciones <= ITER_MAX:
        raise ErrorDeRespaldo("El encabezado de la copia no es válido.")
    sal, mac, cifrado = datos[i + 4:i + 20], datos[i + 20:i + 52], datos[i + 52:]
    esperado = hmac.new(_llave_mac(frase, sal, iteraciones), datos[:i + 20] + cifrado, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, esperado):
        raise ErrorDeRespaldo("La copia está alterada o la frase no es la correcta: no se descifra.")
    plano = _descifrar(cifrado, Path(frase_ruta), iteraciones)
    if not plano.startswith(b"SQLite format 3\x00"):
        raise ErrorDeRespaldo("Lo descifrado no es una base de datos SQLite.")
    return plano


# ------------------------------------------------------------------ la base
def _conteos(ruta_bd: Path) -> Dict[str, int]:
    con = sqlite3.connect(str(ruta_bd))
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ErrorDeRespaldo("La base de datos no pasa la revisión de integridad.")
        tablas = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
    finally:
        con.close()


def _escribir_privado(destino: Path, contenido: bytes) -> None:
    """Escritura atómica con permisos 600."""
    tmp = destino.with_name(destino.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(contenido); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, destino)


def crear(bd: Path, carpeta: Path, frase_archivo: Path, ahora: Optional[datetime] = None) -> Dict:
    bd, carpeta, frase_archivo = Path(bd), Path(carpeta), Path(frase_archivo)
    frase = leer_frase(frase_archivo)
    if not bd.is_file():
        raise ErrorDeRespaldo(f"No existe la base de datos: {bd.name}")
    carpeta.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(carpeta, 0o700)
    except OSError:
        pass
    temporal = Path(tempfile.mkdtemp(prefix="afbk_", dir=carpeta))
    try:
        os.chmod(temporal, 0o700)
        copia = temporal / "copia.sqlite3"
        origen = sqlite3.connect(str(bd), timeout=30)
        destino = sqlite3.connect(str(copia))
        try:
            origen.backup(destino)                                   # consistente aunque el servicio esté escribiendo
        finally:
            destino.close(); origen.close()
        tablas = _conteos(copia)                                     # si la copia está dañada, aquí se detecta y NO se guarda
        sal = os.urandom(16)
        cifrado = _cifrar(copia, frase_archivo, ITERACIONES)
        cabecera = MAGIA + ITERACIONES.to_bytes(4, "big") + sal
        mac = hmac.new(_llave_mac(frase, sal, ITERACIONES), cabecera + cifrado, hashlib.sha256).digest()
        momento = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
        nombre = momento.strftime("antifraude_%Y%m%d_%H%M%S.afbk")
        final = carpeta / nombre
        _escribir_privado(final, cabecera + mac + cifrado)
        return {"archivo": final, "bytes": final.stat().st_size, "tablas": tablas, "creado": momento.isoformat()}
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def verificar(archivo: Path, frase_archivo: Path) -> Dict[str, int]:
    plano = _abrir(archivo, Path(frase_archivo))
    temporal = Path(tempfile.mkdtemp(prefix="afbk_v_"))
    try:
        os.chmod(temporal, 0o700)
        copia = temporal / "v.sqlite3"
        _escribir_privado(copia, plano)
        return _conteos(copia)
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


def restaurar(archivo: Path, destino: Path, frase_archivo: Path, forzar: bool = False) -> Dict:
    destino = Path(destino)
    conteos = verificar(archivo, frase_archivo)                      # primero se prueba que la copia sirve
    plano = _abrir(archivo, Path(frase_archivo))
    apartado = None
    if destino.exists():
        if not forzar:
            raise ErrorDeRespaldo(f"Ya existe {destino.name}. Usa --forzar para apartarla y restaurar encima (la actual se conserva con otro nombre).")
        apartado = destino.with_name(f"{destino.name}.antes_de_restaurar_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}")
        destino.rename(apartado)
    for sufijo in ("-wal", "-shm"):                                   # restos de la base anterior que no corresponden a la copia
        resto = destino.with_name(destino.name + sufijo)
        if resto.exists():
            resto.rename(resto.with_name(resto.name + f".antes_de_restaurar_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"))
    destino.parent.mkdir(parents=True, exist_ok=True)
    _escribir_privado(destino, plano)
    return {"destino": destino, "apartada": apartado, "tablas": conteos}


# ------------------------------------------------------------------ rotación y copia externa
def listar(carpeta: Path) -> List[Tuple[datetime, Path]]:
    salida = []
    for p in Path(carpeta).glob("antifraude_*.afbk"):
        m = PATRON.match(p.name)
        if m:
            salida.append((datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"), p))
    return sorted(salida, reverse=True)


def rotar(carpeta: Path, diarios: int = 7, semanales: int = 4, mensuales: int = 3, simular: bool = False) -> Dict[str, List[Path]]:
    """Conserva la más reciente de cada uno de los últimos `diarios` días, `semanales` semanas y `mensuales` meses; borra el resto. Nunca borra la más reciente."""
    copias = listar(carpeta)
    guardar = set(p for _, p in copias[:1])
    for clave, cuantas in ((lambda d: d.date(), diarios), (lambda d: d.isocalendar()[:2], semanales), (lambda d: (d.year, d.month), mensuales)):
        vistos = []
        for d, p in copias:                                          # de la más nueva a la más vieja: la primera de cada periodo es la más reciente
            k = clave(d)
            if k not in vistos:
                vistos.append(k)
                if len(vistos) <= cuantas:
                    guardar.add(p)
    borrar = [p for _, p in copias if p not in guardar]
    if not simular:
        for p in borrar:
            p.unlink()
    return {"conservadas": [p for _, p in copias if p in guardar], "borradas": borrar}


def copiar_afuera(archivo: Path) -> None:
    """Ejecuta el comando configurado en AFBK_COMANDO_DESPUES (sin shell), reemplazando {archivo}. Por ejemplo:  rclone copy {archivo} remoto:respaldos"""
    comando = os.environ.get("AFBK_COMANDO_DESPUES", "").strip()
    if not comando:
        return
    partes = [x.replace("{archivo}", str(archivo)) for x in shlex.split(comando)]
    try:
        subprocess.run(partes, check=True, timeout=1800, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        raise ErrorDeRespaldo(f"La copia se creó, pero FALLÓ el comando para copiarla afuera ({type(e).__name__}).")


def _anotar_estado(carpeta: Path, ok: bool, archivo: Optional[Path], error: Optional[str]) -> None:
    estado = {"ok": ok, "momento": datetime.now(timezone.utc).isoformat(), "archivo": archivo.name if archivo else None, "error": error}
    _escribir_privado(Path(carpeta) / ESTADO, json.dumps(estado).encode("utf-8"))


def una_vuelta(bd: Path, carpeta: Path, frase_archivo: Path, diarios: int, semanales: int, mensuales: int) -> Dict:
    try:
        r = crear(bd, carpeta, frase_archivo)
        rotar(carpeta, diarios, semanales, mensuales)
        copiar_afuera(r["archivo"])
        _anotar_estado(carpeta, True, r["archivo"], None)
        return r
    except ErrorDeRespaldo as e:
        _anotar_estado(carpeta, False, None, str(e))
        raise


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    e = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m modelo_ia.despliegue.respaldo", description="Copias de seguridad cifradas de la base de datos.")
    ap.add_argument("--bd", type=Path, default=Path(e("MODELO_IA_BD", "/datos/api.sqlite3")))
    ap.add_argument("--carpeta", type=Path, default=Path(e("MODELO_IA_RESPALDOS", "/respaldos")))
    ap.add_argument("--frase-archivo", type=Path, default=Path(e("MODELO_IA_RESPALDO_FRASE_ARCHIVO", "/run/secrets/respaldo_frase")))
    ap.add_argument("--diarios", type=int, default=7); ap.add_argument("--semanales", type=int, default=4); ap.add_argument("--mensuales", type=int, default=3)
    sub = ap.add_subparsers(dest="orden", required=True)
    sub.add_parser("crear")
    v = sub.add_parser("verificar"); v.add_argument("archivo", type=Path)
    r = sub.add_parser("restaurar"); r.add_argument("archivo", type=Path); r.add_argument("--destino", type=Path, default=None); r.add_argument("--forzar", action="store_true")
    ro = sub.add_parser("rotar"); ro.add_argument("--simular", action="store_true")
    c = sub.add_parser("ciclo"); c.add_argument("--cada-horas", type=float, default=6.0)
    a = ap.parse_args(argv)
    try:
        if a.orden == "crear":
            x = una_vuelta(a.bd, a.carpeta, a.frase_archivo, a.diarios, a.semanales, a.mensuales)
            print(f"Copia creada: {x['archivo'].name} ({x['bytes']:,} bytes)".replace(",", ".")); print("Filas por tabla:", ", ".join(f"{k}={v}" for k, v in x["tablas"].items()))
        elif a.orden == "verificar":
            print("Copia VÁLIDA. Filas por tabla:", ", ".join(f"{k}={v}" for k, v in verificar(a.archivo, a.frase_archivo).items()))
        elif a.orden == "restaurar":
            x = restaurar(a.archivo, a.destino or a.bd, a.frase_archivo, a.forzar)
            print(f"Restaurada en {x['destino']}." + (f" La anterior quedó en {x['apartada'].name}." if x["apartada"] else ""))
            print("Filas por tabla:", ", ".join(f"{k}={v}" for k, v in x["tablas"].items()))
        elif a.orden == "rotar":
            x = rotar(a.carpeta, a.diarios, a.semanales, a.mensuales, a.simular)
            print(("Se borrarían" if a.simular else "Borradas") + f": {len(x['borradas'])} | conservadas: {len(x['conservadas'])}")
        elif a.orden == "ciclo":
            while True:
                try:
                    x = una_vuelta(a.bd, a.carpeta, a.frase_archivo, a.diarios, a.semanales, a.mensuales)
                    log.info("Copia creada: %s", x["archivo"].name)
                except ErrorDeRespaldo as err:
                    log.error("Falló la copia: %s", err)                 # se reintenta en la próxima vuelta; el estado queda anotado para la vigilancia
                time.sleep(max(60.0, a.cada_horas * 3600))
        return 0
    except ErrorDeRespaldo as err:
        print("ERROR:", err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
