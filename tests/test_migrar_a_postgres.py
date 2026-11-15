"""La herramienta que pasa los datos de SQLite a una base NUEVA de PostgreSQL (cambio 7c). Necesita MODELO_IA_PRUEBAS_PG (la CI lo tiene).

El origen es un SQLite de verdad (un archivo que termina en «.origen-sqlite»: la escotilla de tests/conftest.py) y el destino un esquema propio de PostgreSQL.
"""

import hashlib
import json
import os
import uuid
from datetime import date
from pathlib import Path

import pytest

from modelo_ia.api import migrar_a_postgres as mp
from modelo_ia.api.almacen import Almacen
from modelo_ia.api.migrar_a_postgres import MigracionDeDatosError, copiar

URL = os.environ.get("MODELO_IA_PRUEBAS_PG")
PEPPER = "p" * 40
pytestmark = [pytest.mark.skipif(not URL, reason="sin MODELO_IA_PRUEBAS_PG"), pytest.mark.migra_datos]


def sha(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def crear_esquema():
    import psycopg

    esquema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{esquema}"')
    return esquema


def borrar_esquema(esquema):
    import psycopg

    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')


@pytest.fixture
def destino():
    esquema = crear_esquema()
    yield Almacen(f"{URL}?options=-csearch_path%3D{esquema}", PEPPER)
    borrar_esquema(esquema)


def evaluacion(alm, comercio, n, reglas):
    with alm.transaccion() as con:
        alm.evaluaciones.insertar(
            con,
            id=f"{n:08d}-0000-4000-8000-000000000000",
            comercio_id=comercio,
            id_transaccion=f"T{n}",
            id_cliente=f"C{n % 7}",
            fecha=f"2026-11-{1 + n % 20:02d}T10:00:00",
            huella=None,
            fila_json=json.dumps({"n": n, "acento": "señal ñandú"}),
            solicitud_hash=f"h{n}",
            decision="EN_REVISION" if n % 3 == 0 else "APROBADA",
            decision_modelo=None,
            probabilidad=None if n % 11 == 0 else (n % 10) / 10,
            motivos_json="[]",
            reglas_json=json.dumps([{"regla": r} for r in reglas]),
            modelo_nombre="gb",
            modelo_version="0.3.0",
            modelo_conjunto="completo",
            modo="normal",
            latencia_ms=1.5,
            creada=f"2026-11-{1 + n % 20:02d}T10:00:00",
            red_origen="comercio",
            estado_origen="comercio",
            id_seguimiento=f"AF-{n:04d}-ABCD",
            ip_codigo=None,
            disp_codigo=None,
            monto_cop=1000.5 * n,
            no_enviados_json="[]",
            senales_json=None,
        )
        if n % 3 == 0:
            alm.revisiones.crear_pendiente(con, f"{n:08d}-0000-4000-8000-000000000000", comercio, "2026-11-12T11:00:00")


@pytest.fixture
def origen(tmp_path):
    ruta = tmp_path / "origen.origen-sqlite"
    a = Almacen(ruta, PEPPER)
    a.inicializar()
    c1 = a.crear_comercio("Tienda Ñandú", "Ñandú S.A.S.", "900.123.456-7")
    c2 = a.crear_comercio("Tienda Dos")
    a.claves_creadas = {
        "comercio": a.crear_clave(c1, "comercio"),
        "analista": a.crear_clave(1, "analista", "Ana", None),
    }
    a.registrar_acuerdo(c1, "v1", date(2026, 1, 1), None, "ref-1")
    for n in range(1, 41):
        evaluacion(a, c1 if n % 2 else c2, n, ["R1", "R2"] if n % 4 == 0 else ["R1"])
    with a.transaccion() as con:
        a.revisiones.resolver(con, f"{3:08d}-0000-4000-8000-000000000000", "APROBADA", "ok", 1, "2026-11-12T11:30:00")
        a.lista_negra.agregar(con, c1, "C3", "motivo con acento: señal", "2026-11-12T12:00:00")
        con.execute(
            "INSERT INTO resultados (id_evaluacion, comercio_id, resultado, fecha_confirmacion, nota, recibido) VALUES (?,?,?,?,?,?)",
            (f"{6:08d}-0000-4000-8000-000000000000", c2, "FRAUDE", "2026-11-13", None, "2026-11-13T09:00:00"),
        )
    a.registro_modelos.registrar(
        "m.joblib", "a" * 64, "gb", "0.3.0", "completo", "principal", {"n": 3, "proporciones": [0.1] * 10}
    )
    a.registro_modelos.promover(1)
    a.origen_ruta = ruta
    return a


def contar(alm, tabla):
    with alm.lectura() as con:
        return con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


def test_copia_todas_las_tablas_con_el_mismo_contenido_y_deja_las_claves_funcionando(origen, destino):
    antes = sha(origen.origen_ruta)
    resumen = copiar(origen.origen_ruta, destino)
    assert sha(origen.origen_ruta) == antes  # el archivo de SQLite quedó exactamente igual
    assert set(resumen) == {t for t, _, _ in mp.TABLAS}
    for tabla, _, _ in mp.TABLAS:
        assert resumen[tabla]["filas"] == contar(origen, tabla) == contar(destino, tabla), tabla
    assert (
        resumen["evaluaciones"]["filas"] == 40
        and resumen["evaluacion_reglas"]["filas"] == 50
        and resumen["revisiones"]["filas"] == 13
    )
    # lo guardado sigue valiendo: la clave entra, el comercio conserva su NIT y los acentos salen tal cual
    assert (
        destino.autenticar(origen.claves_creadas["comercio"]) is not None
        and destino.autenticar(origen.claves_creadas["analista"]) is not None
    )
    with destino.lectura() as con:
        nombre, nit = tuple(
            con.execute("SELECT razon_social, nit FROM comercios WHERE nombre = 'Tienda Ñandú'").fetchone()
        )
        assert (nombre, nit) == ("Ñandú S.A.S.", "900.123.456-7")
        assert con.execute("SELECT estado FROM modelos_registro WHERE id = 1").fetchone()[0] == "en_uso"
        fila = con.execute("SELECT fila_json FROM evaluaciones WHERE id_seguimiento = 'AF-0001-ABCD'").fetchone()[0]
        assert json.loads(fila)["acento"] == "señal ñandú"


def test_despues_de_copiar_los_registros_nuevos_siguen_la_numeracion_sin_chocar(origen, destino):
    copiar(origen.origen_ruta, destino)
    mayor = max(c["id"] for c in origen.listar_comercios())
    assert destino.crear_comercio("Tienda nueva") == mayor + 1  # la secuencia siguió DESPUÉS del último id copiado
    clave = destino.crear_clave(mayor + 1, "comercio")
    assert destino.autenticar(clave) is not None
    with destino.transaccion() as con:  # y la auditoría y los modelos también
        destino.auditar(con, None, None, "prueba", None, {})
    assert destino.registro_modelos.registrar("n.joblib", "b" * 64, "gb", "0.4.0", "completo", "principal") == 2


def test_una_segunda_copia_sobre_el_mismo_destino_se_niega_sin_tocar_nada(origen, destino):
    copiar(origen.origen_ruta, destino)
    filas = contar(destino, "evaluaciones")
    with pytest.raises(MigracionDeDatosError, match="ya tiene datos"):
        copiar(origen.origen_ruta, destino)
    assert contar(destino, "evaluaciones") == filas


def test_un_origen_que_no_esta_al_dia_se_niega_y_dice_que_hacer(tmp_path, destino):
    ruta = tmp_path / "viejo.origen-sqlite"
    v3 = Almacen(ruta, PEPPER)
    assert v3.inicializar(v3._migraciones()[:3]) == [1, 2, 3]
    with pytest.raises(MigracionDeDatosError, match="admin migrar"):
        copiar(ruta, destino)
    assert contar(destino, "evaluaciones") == 0
    with pytest.raises(MigracionDeDatosError, match="No existe el archivo"):
        copiar(tmp_path / "no_existe.sqlite3", destino)


def test_si_la_verificacion_no_coincide_se_deshace_todo_lo_copiado(origen, destino, monkeypatch):
    real = mp.huella
    llamadas = {"n": 0}

    def torcida(filas):
        n, h = real(filas)
        llamadas["n"] += 1
        return (n, h) if llamadas["n"] % 2 else (n, "0" * 64)  # la del destino sale distinta

    monkeypatch.setattr(mp, "huella", torcida)
    with pytest.raises(MigracionDeDatosError, match="NO coincide"):
        copiar(origen.origen_ruta, destino)
    for tabla in ("comercios", "claves", "evaluaciones", "auditoria"):
        assert contar(destino, tabla) <= 1, tabla  # solo queda PLATAFORMA: nada a medias


def test_si_las_columnas_de_los_dos_motores_no_coinciden_se_niega(origen, destino):
    destino.inicializar()
    with destino.transaccion() as con:
        con.execute("ALTER TABLE evaluaciones ADD COLUMN columna_extra TEXT")
    with pytest.raises(MigracionDeDatosError, match="no tiene las mismas columnas"):
        copiar(origen.origen_ruta, destino)
    assert contar(destino, "evaluaciones") == 0


def test_el_destino_debe_ser_postgresql(origen, tmp_path):
    otro = Almacen(tmp_path / "otro.origen-sqlite", PEPPER)
    with pytest.raises(MigracionDeDatosError, match="PostgreSQL"):
        copiar(origen.origen_ruta, otro)


def test_la_huella_depende_del_contenido_y_del_orden_y_no_del_tipo_de_numero():
    a = mp.huella(iter([(1, "x", 2.0), (2, "y", None)]))
    assert a == mp.huella(iter([(1, "x", 2.0), (2, "y", None)])) and a[0] == 2
    assert a != mp.huella(iter([(2, "y", None), (1, "x", 2.0)])) and a != mp.huella(
        iter([(1, "x", 2.5), (2, "y", None)])
    )


def test_si_el_sqlite_cambia_mientras_se_copia_no_se_confirma_nada_y_se_pide_repetir(origen, destino, monkeypatch):
    """El hueco que halló la revisión: el servicio sigue escribiendo, se copia una tabla y luego entra un registro nuevo en otra: antes se imprimía «Listo» y esa fila se perdía."""
    import sqlite3

    real = mp._filas
    estado = {"escrito": False}

    def con_escritura(cur, tabla, columnas, orden):
        yield from real(cur, tabla, columnas, orden)
        if (
            tabla == "comercios" and not estado["escrito"]
        ):  # recién copiada la primera tabla, alguien guarda algo en una que aún no se ha copiado
            estado["escrito"] = True
            otra = sqlite3.connect(origen.origen_ruta)
            otra.execute(
                "INSERT INTO lista_negra (comercio_id, id_cliente, motivo, creada) VALUES (1, 'NUEVO', 'llegó durante la copia', '2026-11-15T00:00:00')"
            )
            otra.commit()
            otra.close()

    monkeypatch.setattr(mp, "_filas", con_escritura)
    with pytest.raises(MigracionDeDatosError, match="CAMBIÓ mientras se copiaba"):
        copiar(origen.origen_ruta, destino)
    for tabla in ("comercios", "claves", "evaluaciones", "auditoria", "lista_negra"):
        assert contar(destino, tabla) <= 1, tabla  # nada a medias en PostgreSQL
    monkeypatch.setattr(mp, "_filas", real)  # y ya sin escrituras, repetir funciona y trae también lo que llegó
    resumen = copiar(origen.origen_ruta, destino)
    assert resumen["lista_negra"]["filas"] == 2
