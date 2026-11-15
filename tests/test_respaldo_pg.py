"""El respaldo cifrado de PostgreSQL (despliegue/respaldo_pg.py): copia con pg_dump, mismo formato .afbk que el de SQLite, restauración en una transacción.

Necesita MODELO_IA_PRUEBAS_PG y las herramientas pg_dump y pg_restore (la CI las trae). En un computador sin ellas se usan las del contenedor Docker de
pruebas (`pg_antifraude`) mediante un envoltorio; si tampoco hay, las pruebas se saltan.
"""

import os
import shutil
import stat
import subprocess
import sys
import uuid

import pytest

from pathlib import Path

from modelo_ia.despliegue import respaldo_pg as rpg
from modelo_ia.despliegue.respaldo import ErrorDeRespaldo

URL = os.environ.get("MODELO_IA_PRUEBAS_PG")
FRASE = "una frase de respaldo muy larga solo para esta prueba"
CONTENEDOR = "pg_antifraude"
ENVOLTORIO = """#!{python}
import os, subprocess, sys
herramienta, args = os.path.basename(sys.argv[0]), sys.argv[1:]
limpio, entrada, salida, i = [], None, None, 0
while i < len(args):
    a = args[i]
    if a in ("-h", "-p"):
        i += 2; continue
    if a == "--file":
        salida = args[i + 1]; i += 2; continue
    limpio.append(a); i += 1
if herramienta == "pg_restore" and limpio and os.path.isfile(limpio[-1]):
    entrada = limpio.pop()
cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD", "{contenedor}", herramienta, "-h", "127.0.0.1", "-p", "5432"] + limpio
r = subprocess.run(cmd, stdin=open(entrada, "rb") if entrada else None, capture_output=True)
if salida:
    open(salida, "wb").write(r.stdout)
else:
    sys.stdout.buffer.write(r.stdout)
sys.stderr.buffer.write(r.stderr)
sys.exit(r.returncode)
"""


def _hay_docker():
    try:
        return (
            CONTENEDOR
            in subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, timeout=20
            ).stdout.decode()
        )
    except Exception:
        return False


@pytest.fixture
def herramientas(tmp_path, monkeypatch):
    if not URL:
        pytest.skip("sin MODELO_IA_PRUEBAS_PG")
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return
    if not _hay_docker():
        pytest.skip("no hay pg_dump/pg_restore ni el contenedor de pruebas")
    carpeta = tmp_path / "bin"
    carpeta.mkdir()
    for nombre in ("pg_dump", "pg_restore"):
        ruta = carpeta / nombre
        ruta.write_text(ENVOLTORIO.format(python=sys.executable, contenedor=CONTENEDOR))
        ruta.chmod(ruta.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv(nombre.upper(), str(ruta))


@pytest.fixture
def base(herramientas, tmp_path):
    """Un esquema de PostgreSQL con la base migrada y datos, y su dirección; se borra al terminar."""
    import psycopg

    from modelo_ia.api.almacen import Almacen

    esquema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{esquema}"')
    url = f"{URL}?options=-csearch_path%3D{esquema}"
    a = Almacen(url, "p" * 40)
    a.inicializar()
    comercio = a.crear_comercio("Tienda Ñandú")
    a.crear_clave(comercio, "comercio")
    frase = tmp_path / "frase"
    frase.write_text(FRASE)
    frase.chmod(0o600)
    yield a, url, esquema, frase
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')


def contar(alm, tabla):
    with alm.lectura() as con:
        return con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


# ------------------------------------------------------------------ sin base de datos
def test_la_direccion_se_parte_sin_dejar_la_contrasena_en_los_argumentos():
    args, entorno, esquema = rpg._partes(
        "postgresql://usuario:CLAVE%40X@db.ejemplo.com:6543/antifraude?sslmode=verify-full&options=-csearch_path%3Dmi_esquema"
    )
    assert args == ["-h", "db.ejemplo.com", "-p", "6543", "-d", "antifraude", "-U", "usuario"]
    assert (
        "CLAVE" not in " ".join(args)
        and entorno == {"PGPASSWORD": "CLAVE@X", "PGSSLMODE": "verify-full"}
        and esquema == "mi_esquema"
    )
    assert rpg._partes("postgresql://u@h/b")[1] == {} and rpg._partes("postgresql://u@h/b")[2] is None
    for mala in ("sqlite:///x", "postgresql://u@h", "no es una dirección"):
        with pytest.raises(ErrorDeRespaldo, match="no es válida"):
            rpg._partes(mala)


def test_sin_pg_dump_el_error_dice_que_instalar(monkeypatch):
    monkeypatch.setenv("PG_DUMP", "")
    monkeypatch.setattr(rpg.shutil, "which", lambda _: None)
    with pytest.raises(ErrorDeRespaldo, match="postgresql-client"):
        rpg._herramienta("pg_dump", "PG_DUMP")


def test_la_direccion_se_lee_de_la_variable_o_de_un_archivo_y_si_falta_se_dice(monkeypatch, tmp_path):
    for v in ("MODELO_IA_BD_URL", "MODELO_IA_BD_URL_ARCHIVO"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(ErrorDeRespaldo, match="Define MODELO_IA_BD_URL"):
        rpg._direccion()
    monkeypatch.setenv("MODELO_IA_BD_URL", "postgresql://u@h/b")
    assert rpg._direccion() == "postgresql://u@h/b"
    archivo = tmp_path / "url"
    archivo.write_text("postgresql://otro@h/c\n")
    monkeypatch.setenv("MODELO_IA_BD_URL_ARCHIVO", str(archivo))
    assert rpg._direccion() == "postgresql://otro@h/c"  # el archivo manda, como en los ajustes
    monkeypatch.setenv("MODELO_IA_BD_URL_ARCHIVO", str(tmp_path / "no_existe"))
    with pytest.raises(ErrorDeRespaldo, match="No se puede leer"):
        rpg._direccion()


# ------------------------------------------------------------------ con PostgreSQL
def test_crear_verificar_y_restaurar_devuelven_exactamente_lo_que_habia(base, tmp_path, monkeypatch):
    import psycopg

    alm, url, esquema, frase = base
    antes = {t: contar(alm, t) for t in rpg.TABLAS_ESPERADAS}
    comandos = []
    real = rpg._ejecutar
    monkeypatch.setattr(
        rpg,
        "_ejecutar",
        lambda comando, entorno, que: comandos.append((comando, entorno)) or real(comando, entorno, que),
    )
    carpeta = tmp_path / "respaldos"
    x = rpg.crear(carpeta, frase, url)
    assert (
        x["archivo"].exists()
        and stat.S_IMODE(x["archivo"].stat().st_mode) == 0o600
        and set(rpg.TABLAS_ESPERADAS) <= set(x["tablas"])
    )
    assert all(
        "prueba_local" not in " ".join(c) for c, _ in comandos
    )  # la contraseña viaja por el entorno, no por la línea de comandos
    assert any(e.get("PGPASSWORD") == "prueba_local" for _, e in comandos)
    assert set(rpg.TABLAS_ESPERADAS) <= set(rpg.verificar(x["archivo"], frase))
    assert (
        b"prueba_local" not in x["archivo"].read_bytes() and "Ñandú".encode() not in x["archivo"].read_bytes()
    )  # cifrado de verdad
    with psycopg.connect(URL, autocommit=True) as c:  # se pierde TODO
        c.execute(
            f'DROP SCHEMA "{esquema}" CASCADE'
        )  # la copia trae su propio CREATE SCHEMA: se restaura sobre una base donde ya no está
    rpg.restaurar(x["archivo"], frase, url)
    restauracion = [c for c, _ in comandos if Path(c[0]).name == "pg_restore" and "--list" not in c][-1]
    assert (
        "--single-transaction" in restauracion and "--exit-on-error" in restauracion
    )  # todo o nada: si algo falla, no queda una base a medias
    assert "--clean" not in restauracion  # sin «forzar» no se borra nada
    for t, n in antes.items():
        assert contar(alm, t) == n, t
    assert alm.crear_comercio("Después de restaurar") > max(antes["comercios"], 1)  # las secuencias también volvieron


def test_un_archivo_alterado_o_con_otra_frase_se_rechaza_antes_de_descifrar(base, tmp_path):
    alm, url, esquema, frase = base
    x = rpg.crear(tmp_path / "r", frase, url)
    datos = bytearray(x["archivo"].read_bytes())
    datos[len(datos) // 2] ^= 0xFF
    alterado = tmp_path / "alterado.afbk"
    alterado.write_bytes(bytes(datos))
    with pytest.raises(ErrorDeRespaldo, match="alterada"):
        rpg.verificar(alterado, frase)
    otra = tmp_path / "otra_frase"
    otra.write_text("otra frase distinta pero igual de larga que la de la copia")
    otra.chmod(0o600)
    with pytest.raises(ErrorDeRespaldo):
        rpg.verificar(x["archivo"], otra)


def test_restaurar_sin_forzar_sobre_una_base_con_datos_falla_sin_cambiar_nada_y_con_forzar_la_reemplaza(base, tmp_path):
    alm, url, esquema, frase = base
    x = rpg.crear(tmp_path / "r", frase, url)
    alm.crear_comercio("Posterior a la copia")
    n = contar(alm, "comercios")
    with pytest.raises(ErrorDeRespaldo, match="pg_restore falló"):
        rpg.restaurar(x["archivo"], frase, url)
    assert contar(alm, "comercios") == n  # una sola transacción: nada a medias
    rpg.restaurar(x["archivo"], frase, url, forzar=True)
    assert contar(alm, "comercios") == n - 1  # volvió al momento de la copia


def test_una_base_sin_las_tablas_no_se_respalda_como_si_estuviera_bien(herramientas, tmp_path):
    import psycopg

    esquema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{esquema}"')
        c.execute(f'CREATE TABLE "{esquema}".algo (x int)')
    frase = tmp_path / "frase"
    frase.write_text(FRASE)
    frase.chmod(0o600)
    try:
        with pytest.raises(ErrorDeRespaldo, match="no trae las tablas"):
            rpg.crear(tmp_path / "r", frase, f"{URL}?options=-csearch_path%3D{esquema}")
        assert not list((tmp_path / "r").glob("*.afbk"))  # y no queda ningún archivo
    finally:
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{esquema}" CASCADE')


@pytest.mark.migra_datos
def test_una_copia_de_sqlite_no_se_acepta_como_de_postgresql_ni_al_reves(base, tmp_path):
    from modelo_ia.api.repositorios.base import Base  # noqa: F401
    from modelo_ia.despliegue import respaldo

    alm, url, esquema, frase = base
    origen = tmp_path / "x.origen-sqlite"
    from modelo_ia.api.almacen import Almacen

    a = Almacen(origen, "p" * 40)
    a.inicializar()
    de_sqlite = respaldo.crear(origen, tmp_path / "rs", frase)["archivo"]
    with pytest.raises(ErrorDeRespaldo, match="copia de PostgreSQL"):
        rpg.verificar(de_sqlite, frase)
    de_pg = rpg.crear(tmp_path / "rp", frase, url)["archivo"]
    with pytest.raises(ErrorDeRespaldo, match="base de datos SQLite"):
        respaldo.verificar(de_pg, frase)
