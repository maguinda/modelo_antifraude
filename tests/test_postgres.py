"""PostgreSQL detrás de la misma interfaz que SQLite (cambio 7b).

Las pruebas de traducción y de filas no necesitan base. Las demás corren solo si `MODELO_IA_PRUEBAS_PG` apunta a una base de PostgreSQL (la CI levanta una);
cada una trabaja en un ESQUEMA propio que borra al terminar. La suite completa sobre PostgreSQL se corre con esa misma variable (ver tests/conftest.py).
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from modelo_ia.api.errores_bd import CUALQUIERA, INTEGRIDAD, OPERACION
from modelo_ia.api.repositorios.motor_pg import BasePg, FilaPg, es_direccion_pg, traducir

URL = os.environ.get("MODELO_IA_PRUEBAS_PG")
RAIZ = Path(__file__).resolve().parents[2]
PEPPER = "p" * 40
necesita_pg = pytest.mark.skipif(not URL, reason="sin MODELO_IA_PRUEBAS_PG (una base de PostgreSQL de prueba)")


# ------------------------------------------------------------------ sin base de datos
def test_traducir_cambia_los_marcadores_pero_no_los_signos_que_estan_dentro_de_un_texto():
    assert traducir("SELECT * FROM t WHERE a=? AND b=?") == "SELECT * FROM t WHERE a=%s AND b=%s"
    assert traducir("SELECT 'a?b' || ? FROM t") == "SELECT 'a?b' || %s FROM t"  # el «?» dentro de comillas es texto
    assert traducir("SELECT 'it''s ?' , ?") == "SELECT 'it''s ?' , %s"  # la comilla doble escapada no cierra el texto
    assert (
        traducir("SELECT 'a%' || ?") == "SELECT 'a%%' || %s"
    )  # el «%» se duplica: el controlador lo tomaría por un marcador


def test_la_fila_se_lee_por_posicion_por_nombre_y_como_diccionario():
    f = FilaPg((7, "x", None), ("id", "nombre", "nota"))
    assert f[0] == 7 and f["nombre"] == "x" and f["nota"] is None and f[-1] is None
    assert (
        dict(f) == {"id": 7, "nombre": "x", "nota": None}
        and f.keys() == ["id", "nombre", "nota"]
        and list(f) == [7, "x", None]
    )
    assert f == (7, "x", None) and tuple(f) == (7, "x", None)
    with pytest.raises(IndexError, match="columna llamada 'otra'"):
        f["otra"]


def test_solo_las_direcciones_de_postgresql_se_reconocen_como_tales():
    assert es_direccion_pg("postgresql://u:p@h:5432/b") and es_direccion_pg("postgres://u@h/b")
    assert (
        not es_direccion_pg(Path("/tmp/x.sqlite3"))
        and not es_direccion_pg("/tmp/x.sqlite3")
        and not es_direccion_pg(None)
    )


def test_la_contrasena_no_sale_en_la_descripcion_de_la_base():
    base = BasePg("postgresql://usuario:secreta@servidor:5432/antifraude")
    assert "secreta" not in base.describir() and base.describir() == "postgresql://usuario:***@servidor:5432/antifraude"


def test_el_nombre_del_esquema_se_valida_para_que_no_pueda_inyectar_sql():
    for malo in ('a"; DROP SCHEMA public; --', "Mayusculas", "con espacio", "", "x" * 64):
        with pytest.raises(ValueError):
            BasePg("postgresql://u:p@h/b", esquema=malo)


def test_los_errores_de_los_dos_motores_se_atrapan_con_el_mismo_grupo():
    import sqlite3

    import psycopg

    assert sqlite3.IntegrityError in INTEGRIDAD and issubclass(psycopg.errors.UniqueViolation, INTEGRIDAD)
    assert issubclass(sqlite3.OperationalError, OPERACION) and issubclass(psycopg.errors.OperationalError, OPERACION)
    assert issubclass(psycopg.errors.SyntaxError, CUALQUIERA) and issubclass(sqlite3.DatabaseError, CUALQUIERA)


# ------------------------------------------------------------------ con PostgreSQL
@pytest.fixture
def pg():
    """Una dirección de PostgreSQL con un esquema nuevo (va en la propia dirección) que se borra al terminar."""
    import psycopg

    esquema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{esquema}"')
    yield f"{URL}?options=-csearch_path%3D{esquema}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA "{esquema}" CASCADE')


@pytest.fixture
def alm(pg):
    from modelo_ia.api.almacen import Almacen

    a = Almacen(pg, PEPPER)
    a.inicializar()
    return a


@necesita_pg
def test_una_base_nueva_se_migra_a_la_version_actual_una_sola_vez_y_el_estado_lo_dice(pg):
    from modelo_ia.api.almacen import Almacen

    a = Almacen(pg, PEPPER)
    assert [m["aplicada"] is None for m in a.estado_migraciones()] == [
        True
    ] * 4  # antes de migrar: nada aplicado, sin tocar la base
    assert a.inicializar() == [1, 2, 3, 4] and a.inicializar() == []
    assert [m["id"] for m in a.estado_migraciones() if m["aplicada"]] == [1, 2, 3, 4] and a.copias_previas() == []


@necesita_pg
def test_el_comercio_plataforma_existe_y_el_siguiente_comercio_no_choca_con_su_id(alm):
    assert (
        alm.crear_comercio("Tienda PG") == 2
    )  # la secuencia de identidad siguió DESPUÉS del id 1 que se escribió a mano
    assert alm.crear_comercio("Otra") == 3


@necesita_pg
def test_la_auditoria_y_los_acuerdos_no_se_editan_ni_se_borran_y_dicen_por_que(alm):
    from datetime import date

    comercio = alm.crear_comercio("Tienda PG")
    alm.registrar_acuerdo(comercio, "v1", date(2026, 1, 1), None, "ref-1")
    for sql, texto in (
        ("UPDATE auditoria SET accion='x'", "la auditoria no se edita"),
        ("DELETE FROM auditoria", "la auditoria no se borra"),
        ("UPDATE acuerdos SET version='v2'", "los acuerdos no se editan"),
        ("DELETE FROM acuerdos", "los acuerdos no se borran"),
    ):
        with pytest.raises(INTEGRIDAD, match=texto), alm.transaccion() as con:
            con.execute(sql)


@necesita_pg
def test_el_indice_parcial_impide_dos_modelos_en_uso_para_el_mismo_rol(alm):
    r = alm.registro_modelos
    a, b = (
        r.registrar("a.joblib", "a" * 64, "m", "1", "c", "principal"),
        r.registrar("b.joblib", "b" * 64, "m", "2", "c", "principal"),
    )
    r.promover(a)
    with pytest.raises(INTEGRIDAD), alm.transaccion() as con:
        con.execute("UPDATE modelos_registro SET estado='en_uso' WHERE id=?", (b,))  # a mano, saltándose el repositorio


@necesita_pg
def test_los_booleanos_de_python_se_guardan_como_enteros_y_un_texto_con_porcentaje_no_se_rompe(alm):
    c = alm.crear_comercio("Tienda 100% segura ?")
    with alm.lectura() as con:
        fila = con.execute("SELECT nombre, activo FROM comercios WHERE id = ?", (c,)).fetchone()
        assert fila["nombre"] == "Tienda 100% segura ?" and fila["activo"] == 1
        assert (
            con.execute(
                "SELECT COUNT(*) FROM comercios WHERE nombre LIKE '%segura%' AND activo = ?", (True,)
            ).fetchone()[0]
            == 1
        )


@necesita_pg
def test_lo_escrito_en_una_transaccion_que_falla_no_queda(alm):
    with pytest.raises(RuntimeError), alm.transaccion() as con:
        con.execute("INSERT INTO comercios (nombre, creado) VALUES ('Fantasma', 'x')")
        raise RuntimeError("falla a propósito")
    with alm.lectura() as con:
        assert con.execute("SELECT COUNT(*) FROM comercios WHERE nombre='Fantasma'").fetchone()[0] == 0


@necesita_pg
def test_una_lectura_instantanea_no_ve_lo_que_otro_guarda_despues(alm):
    with alm.lectura(instantanea=True) as foto:
        antes = foto.execute("SELECT COUNT(*) FROM comercios").fetchone()[0]
        alm.crear_comercio("Llega después")
        assert foto.execute("SELECT COUNT(*) FROM comercios").fetchone()[0] == antes
    with alm.lectura() as con:
        assert con.execute("SELECT COUNT(*) FROM comercios").fetchone()[0] == antes + 1


def _lanzar(codigo, *args):
    env = {**os.environ, "PYTHONPATH": str(RAIZ)}
    return subprocess.Popen(
        [sys.executable, "-c", codigo, *args], env=env, cwd=str(RAIZ), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


@necesita_pg
def test_tres_procesos_arrancando_a_la_vez_migran_cada_migracion_una_sola_vez(pg):
    codigo = (
        "import sys\nfrom modelo_ia.api.almacen import Almacen\nprint(Almacen(sys.argv[1], 'p' * 40).inicializar())\n"
    )
    for _ in range(5):  # base nueva cada ronda
        import psycopg

        esquema = "t_" + uuid.uuid4().hex[:12]
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f'CREATE SCHEMA "{esquema}"')
        try:
            url = f"{URL}?options=-csearch_path%3D{esquema}"
            hijos = [_lanzar(codigo, url) for _ in range(3)]
            salidas = [h.communicate(timeout=120) for h in hijos]
            assert [h.returncode for h in hijos] == [0, 0, 0], [s[1][-300:] for s in salidas]
            aplicadas = sorted(
                int(n) for out, _ in salidas for n in out.decode().strip("[]\n ").split(",") if n.strip()
            )
            assert aplicadas == [1, 2, 3, 4], aplicadas  # entre los tres, cada migración exactamente una vez
        finally:
            with psycopg.connect(URL, autocommit=True) as c:
                c.execute(f'DROP SCHEMA "{esquema}" CASCADE')


@necesita_pg
def test_tres_procesos_promoviendo_a_la_vez_nunca_dejan_dos_modelos_en_uso(pg):
    from modelo_ia.api.almacen import Almacen

    codigo = (
        "import sys\nfrom modelo_ia.api.almacen import Almacen\na = Almacen(sys.argv[1], 'p' * 40)\n"
        "try:\n    a.registro_modelos.promover(int(sys.argv[2]))\nexcept ValueError:\n    pass\n"
    )
    import psycopg

    for _ in range(8):
        esquema = "t_" + uuid.uuid4().hex[:12]
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f'CREATE SCHEMA "{esquema}"')
        try:
            url = f"{URL}?options=-csearch_path%3D{esquema}"
            base = Almacen(url, PEPPER)
            base.inicializar()
            ids = [
                base.registro_modelos.registrar(f"m{i}.joblib", str(i) * 64, "gb", f"{i}", "c", "principal")
                for i in range(1, 4)
            ]
            hijos = [_lanzar(codigo, url, str(i)) for i in ids]
            assert [h.wait(timeout=120) for h in hijos] == [0, 0, 0]
            assert sorted(m["estado"] for m in base.registro_modelos.listar()) == ["archivado", "archivado", "en_uso"]
        finally:
            with psycopg.connect(URL, autocommit=True) as c:
                c.execute(f'DROP SCHEMA "{esquema}" CASCADE')


@necesita_pg
def test_dos_pagos_iguales_a_la_vez_desde_procesos_distintos_dejan_una_sola_evaluacion(pg):
    """La idempotencia entre PROCESOS: lo que en SQLite lo ordena BEGIN IMMEDIATE, aquí lo ordena el candado de PostgreSQL."""
    from modelo_ia.api.almacen import Almacen

    base = Almacen(pg, PEPPER)
    base.inicializar()
    comercio = base.crear_comercio("Tienda PG")
    codigo = (
        "import sys, json\nfrom modelo_ia.api.almacen import Almacen\na = Almacen(sys.argv[1], 'p' * 40)\nc = int(sys.argv[2])\n"
        "from modelo_ia.api.errores_bd import INTEGRIDAD\nok = 0\n"
        "for i in range(10):\n"
        "    try:\n        with a.transaccion() as con:\n"
        '            con.execute("INSERT INTO evaluaciones (id, comercio_id, id_transaccion, id_cliente, fecha, fila_json, solicitud_hash, decision, motivos_json, reglas_json, "\n'
        "                \"modelo_nombre, modelo_version, modelo_conjunto, modo, latencia_ms, creada, seq) VALUES (?,?,?, 'C', 'f', '{}', 'h', 'APROBADA', '[]', '[]', 'm','1','c','normal',1.0,'f', \"\n"
        "                \"(SELECT COALESCE(MAX(seq),0)+1 FROM evaluaciones))\", (sys.argv[3] + str(i), c, 'TX' + str(i)))\n"
        "        ok += 1\n"
        "    except INTEGRIDAD:\n        pass\nprint(ok)\n"
    )
    hijos = [_lanzar(codigo, pg, str(comercio), f"id{n}-") for n in range(3)]
    salidas = [h.communicate(timeout=120) for h in hijos]
    assert [h.returncode for h in hijos] == [0, 0, 0], [s[1][-300:] for s in salidas]
    assert (
        sum(int(out.decode().strip()) for out, _ in salidas) == 10
    )  # 10 id_transaccion distintos: cada uno entró UNA vez entre los tres procesos
    with base.lectura() as con:
        assert con.execute("SELECT COUNT(*), COUNT(DISTINCT seq) FROM evaluaciones").fetchone().__class__ is not None
        total, distintos = tuple(con.execute("SELECT COUNT(*), COUNT(DISTINCT seq) FROM evaluaciones").fetchone())
    assert total == 10 and distintos == 10  # y ningún seq repetido


# ------------------------------------------------------------------ lo que halló la revisión de seguridad
def test_las_migraciones_de_sqlite_y_de_postgresql_llevan_los_mismos_numeros_y_nombres(tmp_path):
    """El día que alguien agregue la migración 5 solo para SQLite, esta prueba falla: las dos listas deben crecer juntas."""
    from modelo_ia.api.repositorios.base import Base
    from modelo_ia.api.repositorios.esquema import Esquema
    from modelo_ia.api.repositorios.seudonimos import Seudonimizador

    seud = Seudonimizador(PEPPER.encode())
    sqlite = Esquema(Base(tmp_path / "x.sqlite3"), seud).migraciones()
    pg = Esquema(BasePg("postgresql://u:p@h/b"), seud).migraciones()  # no se conecta al crearla
    assert [(m.id, m.nombre) for m in pg] == [(m.id, m.nombre) for m in sqlite]


def test_una_direccion_mal_escrita_no_deja_la_contrasena_en_el_error_ni_en_la_representacion():
    base = BasePg("postgresql://usuario:CLAVE-SECRETA@[::1/x")
    with pytest.raises(ValueError) as e:
        base.abrir()
    assert "CLAVE-SECRETA" not in str(e.value) and "CLAVE-SECRETA" not in repr(e.value) and e.value.__cause__ is None
    assert "CLAVE-SECRETA" not in repr(base)
    from modelo_ia.api.ajustes import Ajustes

    assert "CLAVE-SECRETA" not in repr(Ajustes(bd_url="postgresql://u:CLAVE-SECRETA@h/b"))


def test_una_direccion_de_postgresql_sin_sslmode_fuera_de_la_propia_maquina_da_un_aviso_de_produccion():
    from modelo_ia.api.ajustes import Ajustes

    def avisos(url):
        return [a for a in Ajustes(bd_url=url).problemas_de_produccion()["avisos"] if "sslmode" in a]

    assert avisos("postgresql://u:p@db.ejemplo.com:5432/b")  # remota y sin cifrar: avisa
    assert not avisos("postgresql://u:p@db.ejemplo.com:5432/b?sslmode=verify-full")
    assert (
        not avisos("postgresql://u:p@localhost:5432/b")
        and not avisos("postgresql://u:p@127.0.0.1/b")
        and not avisos(None)
    )


@necesita_pg
def test_truncate_no_puede_vaciar_la_auditoria_ni_los_acuerdos(alm):
    """En SQLite no existe TRUNCATE; en PostgreSQL no dispara los triggers por fila: sin su propio trigger vaciaría la auditoría."""
    alm.crear_comercio("Tienda PG")  # deja filas en auditoría
    for tabla in ("auditoria", "acuerdos"):
        for sentencia in (f"TRUNCATE {tabla}", f"TRUNCATE {tabla} CASCADE"):
            with pytest.raises(INTEGRIDAD, match="no se borra"), alm.transaccion() as con:
                con.execute(sentencia)
    with alm.lectura() as con:
        assert con.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0] >= 1


@necesita_pg
def test_el_servicio_arranca_con_un_rol_que_solo_lee_y_escribe_filas_si_la_base_ya_esta_al_dia(pg):
    """Un rol de aplicación sin CREATE ni TRUNCATE: lo que se recomienda en producción. Solo el que migra necesita CREATE."""
    import psycopg

    from modelo_ia.api.almacen import Almacen

    esquema = pg.split("search_path%3D")[1]
    Almacen(pg, PEPPER).inicializar()  # el dueño migra
    rol = "app_" + uuid.uuid4().hex[:8]
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"CREATE ROLE {rol} LOGIN PASSWORD 'solo_prueba'")
        c.execute(f'GRANT USAGE ON SCHEMA "{esquema}" TO {rol}')
        c.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{esquema}" TO {rol}')
        c.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{esquema}" TO {rol}')
    try:
        host = URL.split("@")[1].split("/")[0]
        url_app = f"postgresql://{rol}:solo_prueba@{host}/{URL.rsplit('/', 1)[1]}?options=-csearch_path%3D{esquema}"
        app = Almacen(url_app, PEPPER)
        assert app.inicializar() == []  # sin CREATE: no intenta crear nada porque ya está al día
        assert app.crear_comercio("Desde el rol de la aplicación") > 1
        with pytest.raises(Exception, match="permission denied"), app.transaccion() as con:
            con.execute("TRUNCATE auditoria")  # y el rol no puede vaciar nada aunque quisiera
    finally:
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{esquema}" FROM {rol}')
            c.execute(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA "{esquema}" FROM {rol}')
            c.execute(f'REVOKE USAGE ON SCHEMA "{esquema}" FROM {rol}')
            c.execute(f"DROP ROLE {rol}")


def test_ocultar_clave_cubre_las_formas_en_que_una_contrasena_puede_ir_en_una_direccion():
    from modelo_ia.api.repositorios.motor_pg import ocultar_clave

    casos = {
        "postgresql://u:SECRETA@h:5432/b": "postgresql://u:***@h:5432/b",
        "postgresql://u:SE@CRETA@h/b": "postgresql://u:***@h/b",  # una clave con «@» sin codificar
        "postgresql://u@h/b?password=SECRETA&sslmode=require": "postgresql://u@h/b?password=***&sslmode=require",
        "postgresql://h/b?sslmode=require&PASSWORD=SECRETA": "postgresql://h/b?sslmode=require&PASSWORD=***",
        "postgresql://u:SECRETA@h/b?options=-csearch_path%3Dx": "postgresql://u:***@h/b?options=-csearch_path%3Dx",
        "postgresql://h/b": "postgresql://h/b",
    }
    for entrada, esperado in casos.items():
        assert ocultar_clave(entrada) == esperado, entrada
        assert "SECRETA" not in ocultar_clave(entrada) and "CRETA" not in ocultar_clave(entrada)


def test_la_conexion_se_considera_sin_cifrar_solo_si_sale_de_la_maquina_y_no_exige_cifrado():
    from modelo_ia.api.repositorios.motor_pg import direccion_sin_cifrar as sin_cifrar

    assert sin_cifrar("postgresql://u:p@db.ejemplo.com/b") and sin_cifrar(
        "postgresql://u:p@db.ejemplo.com/b?sslmode=prefer"
    )
    assert sin_cifrar("postgresql://u:p@db.ejemplo.com/b?sslmode=disable") and sin_cifrar(
        "postgresql://u:p@db.ejemplo.com/b?sslmode=allow"
    )
    assert sin_cifrar("postgresql://u:p@localhost.ejemplo.com/b") and sin_cifrar(
        "postgresql://u:p@127.0.0.1.nip.io/b"
    )  # parecen locales y no lo son
    assert sin_cifrar("postgresql://u:p@[2001:db8::1]/b")
    for bien in ("require", "verify-ca", "verify-full", "VERIFY-FULL"):
        assert not sin_cifrar(f"postgresql://u:p@db.ejemplo.com/b?sslmode={bien}"), bien
    for local in ("postgresql://u:p@localhost/b", "postgresql://u:p@LOCALHOST:5432/b", "postgresql://u:p@127.0.0.1/b", "postgresql://u:p@127.0.0.2/b",
                  "postgresql://u:p@[::1]:5432/b", "postgresql:///b?host=/var/run/postgresql", "postgresql://u@/b?host=/tmp"):  # fmt: skip
        assert not sin_cifrar(local), local
