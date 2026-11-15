"""El registro de modelos con huella SHA-256, la carga verificada y la consola. Lo central: un modelo cuya huella no coincide NUNCA se deserializa
(`joblib.load` ejecuta código) y el servicio no arranca; promover y revertir no dejan nunca dos modelos en uso ni ninguno a medias."""
# ruff: noqa: F811  (el fixture `artefacto` se importa y se pide por nombre en los parámetros de las pruebas)

import json
import logging
import re
import sqlite3
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

import joblib
import pytest

from modelo_ia import config, deriva
from modelo_ia.api import admin, registro_modelos as rm
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.almacen import Almacen
from modelo_ia.api.app import crear_app
from modelo_ia.api.registro_modelos import ModeloAlterado, RegistroIncompleto
from modelo_ia.tests.test_api import RELOJ, artefacto, construir_contexto  # noqa: F401  (artefacto es un fixture)

PEPPER = "pepper-de-prueba"


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """Una carpeta de modelos desechable: el código la lee de `config.CARPETA_MODELOS`."""
    c = tmp_path / "modelos_guardados"
    c.mkdir()
    monkeypatch.setattr(config, "CARPETA_MODELOS", c)
    return c


@pytest.fixture
def alm(tmp_path):
    a = Almacen(tmp_path / "r.sqlite3", PEPPER)
    a.inicializar()
    return a


def guardar(carpeta, art, nombre, version="0.9.0", sin_estado=False, referencia=None, escribir_json=True):
    """Guarda un modelo (el de las pruebas) como lo hace `entrenar.guardar`: el .joblib y su .json."""
    meta = dict(art["meta"], version=version, sin_estado=sin_estado)
    if referencia is not None:
        meta["referencia_puntaje"] = referencia
    ruta = carpeta / f"{nombre}.joblib"
    joblib.dump({"pipeline": art["pipeline"], "meta": meta}, ruta)
    if escribir_json:
        ruta.with_suffix(".json").write_text(
            json.dumps({k: v for k, v in meta.items() if k != "referencia"}), encoding="utf-8"
        )
    return ruta


REF = {"n": 1000, "proporciones": [0.5, 0.2, 0.1, 0.05, 0.05, 0.03, 0.03, 0.02, 0.01, 0.01]}


# ------------------------------------------------------------------ el registro (base de datos)
def test_registrar_promover_archivar_y_revertir(alm):
    r = alm.registro_modelos
    a = r.registrar("a.joblib", "a" * 64, "gb", "0.3.0", "completo", "principal", REF)
    b = r.registrar("b.joblib", "b" * 64, "gb", "0.4.0", "completo", "principal")
    otro = r.registrar("s.joblib", "c" * 64, "gb", "0.3.0", "completo", "sin_estado")
    assert [m["estado"] for m in r.listar()] == ["archivado"] * 0 + ["candidato"] * 3 and r.en_uso("principal") is None
    assert (
        r.promover(a)["estado"] == "en_uso"
        and r.en_uso("principal")["id"] == a
        and r.en_uso("principal")["referencia"] == REF
    )
    assert r.promover(b)["estado"] == "en_uso"
    estados = {m["id"]: m["estado"] for m in r.listar()}
    assert estados == {a: "archivado", b: "en_uso", otro: "candidato"}  # el anterior se archiva; el otro rol ni se toca
    assert r.en_uso("sin_estado") is None
    vuelto = r.revertir("principal")
    assert vuelto["id"] == a and {m["id"]: m["estado"] for m in r.listar()} == {
        a: "en_uso",
        b: "archivado",
        otro: "candidato",
    }
    assert (
        r.revertir("principal")["id"] == b
    )  # volver a revertir regresa al otro (el archivado más recientemente promovido)


def test_los_errores_del_registro_son_claros(alm):
    r = alm.registro_modelos
    a = r.registrar("a.joblib", "a" * 64, "gb", "0.3.0", "completo", "principal")
    with pytest.raises(ValueError, match="ya está registrado"):
        r.registrar("a2.joblib", "a" * 64, "gb", "0.3.0", "completo", "principal")  # la misma huella para el mismo rol
    assert r.registrar("a.joblib", "a" * 64, "gb", "0.3.0", "completo", "sin_estado")  # pero otro rol sí
    with pytest.raises(ValueError, match="rol debe ser"):
        r.registrar("x.joblib", "d" * 64, "gb", "0.3.0", "completo", "inventado")
    with pytest.raises(ValueError, match="No existe"):
        r.promover(999)
    r.promover(a)
    with pytest.raises(ValueError, match="ya está en uso"):
        r.promover(a)
    with pytest.raises(ValueError, match="No hay un modelo anterior"):
        r.revertir("principal")
    with pytest.raises(ValueError, match="rol debe ser"):
        r.revertir("otro")
    assert r.por_id(999) is None and r.por_id(a)["archivo"] == "a.joblib"
    with pytest.raises(ValueError, match="No existe"):
        r.guardar_referencia(999, REF)


def test_la_base_impide_dos_modelos_en_uso_para_el_mismo_rol_aunque_se_intente_a_mano(alm):
    r = alm.registro_modelos
    a = r.registrar("a.joblib", "a" * 64, "gb", "0.3.0", "completo", "principal")
    b = r.registrar("b.joblib", "b" * 64, "gb", "0.4.0", "completo", "principal")
    con = sqlite3.connect(alm.base.ruta)
    con.execute("UPDATE modelos_registro SET estado='en_uso' WHERE id=?", (a,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE modelos_registro SET estado='en_uso' WHERE id=?", (b,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE modelos_registro SET estado='cualquier_cosa' WHERE id=?", (b,))


def test_cada_cambio_queda_en_la_auditoria_sin_la_huella_completa(alm):
    r = alm.registro_modelos
    a = r.registrar("a.joblib", "a1b2c3d4e5f6" + "0" * 52, "gb", "0.3.0", "completo", "principal", REF)
    b = r.registrar("b.joblib", "f" * 64, "gb", "0.4.0", "completo", "principal")
    r.promover(a), r.promover(b), r.revertir("principal"), r.guardar_referencia(b, REF)
    filas = (
        sqlite3.connect(alm.base.ruta)
        .execute("SELECT accion, detalle FROM auditoria WHERE accion LIKE 'modelo_%' ORDER BY id")
        .fetchall()
    )
    assert [f[0] for f in filas] == [
        "modelo_registrado",
        "modelo_registrado",
        "modelo_promovido",
        "modelo_promovido",
        "modelo_revertido",
        "modelo_referencia_guardada",
    ]
    assert "a1b2c3d4e5f6" in filas[0][1] and "a1b2c3d4e5f6" + "0" * 20 not in " ".join(
        f[1] for f in filas
    )  # solo los 12 primeros caracteres de la huella


def test_dos_promociones_a_la_vez_nunca_dejan_dos_en_uso_ni_ninguno_en_60_rondas(tmp_path):
    for ronda in range(60):
        base = Almacen(tmp_path / f"c{ronda}.sqlite3", PEPPER)
        base.inicializar()
        otra = Almacen(tmp_path / f"c{ronda}.sqlite3", PEPPER)  # otro «proceso»: candado propio, solo SQLite los ordena
        a = base.registro_modelos.registrar("a.joblib", "a" * 64, "gb", "1", "c", "principal")
        b = base.registro_modelos.registrar("b.joblib", "b" * 64, "gb", "2", "c", "principal")
        c = base.registro_modelos.registrar("c.joblib", "c" * 64, "gb", "3", "c", "principal")
        base.registro_modelos.promover(a)

        def promover(par):
            alm, id_ = par
            try:
                return alm.registro_modelos.promover(id_)["id"]
            except ValueError as e:
                return str(e)

        with ThreadPoolExecutor(3) as pool:
            list(pool.map(promover, [(base, b), (otra, c), (otra, b)]))
        estados = [m["estado"] for m in base.registro_modelos.listar()]
        assert estados.count("en_uso") == 1, (ronda, estados)  # exactamente uno
        assert base.registro_modelos.en_uso("principal")["id"] in (b, c)


# ------------------------------------------------------------------ verificación de la huella
def test_leer_verificado_entrega_los_bytes_solo_si_la_huella_coincide(carpeta, artefacto):  # noqa: F811
    ruta = guardar(carpeta, artefacto, "m1")
    huella = rm.sha256_de(ruta)
    assert (
        rm.leer_verificado(ruta, huella) == ruta.read_bytes()
        and len(huella) == 64
        and huella == rm.sha256_de_bytes(ruta.read_bytes())
    )
    ruta.write_bytes(ruta.read_bytes() + b"\x00")  # un solo byte de más
    with pytest.raises(ModeloAlterado, match="NO coincide con el que se registró") as e:
        rm.leer_verificado(ruta, huella)
    assert huella not in str(e.value) and huella[:12] in str(
        e.value
    )  # el mensaje trae 12 caracteres, no la huella completa
    ruta.unlink()
    with pytest.raises(ModeloAlterado, match="No se pudo leer"):
        rm.leer_verificado(ruta, huella)


@pytest.mark.parametrize(
    "malo",
    ["../x.joblib", "/etc/passwd.joblib", "a/b.joblib", ".oculto.joblib", "x.pkl", "", "x.joblib/", "..\\x.joblib"],
)
def test_el_registro_solo_guarda_nombres_de_archivo_no_rutas(malo):
    with pytest.raises(ValueError, match="Nombre de archivo"):
        rm.ruta_del_modelo(malo)
    assert rm.ruta_del_modelo("gb_completo_v0.3.0.joblib").name == "gb_completo_v0.3.0.joblib"


# ------------------------------------------------------------------ registrar y adoptar
def test_registrar_archivo_toma_los_datos_del_json_y_deduce_el_rol(carpeta, alm, artefacto):  # noqa: F811
    ruta = guardar(carpeta, artefacto, "gb_completo", referencia=REF)
    id_ = rm.registrar_archivo(alm, ruta)
    m = alm.registro_modelos.por_id(id_)
    assert (m["archivo"], m["rol"], m["estado"], m["version"], m["sha256"], m["referencia"]) == (
        "gb_completo.joblib",
        "principal",
        "candidato",
        "0.9.0",
        rm.sha256_de(ruta),
        REF,
    )
    sin = guardar(carpeta, artefacto, "gb_sinestado", sin_estado=True)
    assert alm.registro_modelos.por_id(rm.registrar_archivo(alm, sin))["rol"] == "sin_estado"
    with pytest.raises(ValueError, match="es «sin_estado»"):
        rm.registrar_archivo(
            alm, guardar(carpeta, artefacto, "otro_sin", sin_estado=True, version="1.0.0"), "principal"
        )  # un sin_estado no puede ser principal
    with pytest.raises(ValueError, match="es «principal»"):
        rm.registrar_archivo(alm, guardar(carpeta, artefacto, "otro_pri", version="1.1.0"), "sin_estado")


def test_registrar_archivo_rechaza_lo_que_no_es_un_modelo_de_la_carpeta(carpeta, alm, artefacto, tmp_path):  # noqa: F811
    fuera = tmp_path / "fuera.joblib"
    fuera.write_bytes(b"x")
    with pytest.raises(ValueError, match="carpeta de modelos"):
        rm.registrar_archivo(alm, fuera)
    with pytest.raises(ValueError, match="No existe"):
        rm.registrar_archivo(alm, carpeta / "no_esta.joblib")
    sin_json = guardar(carpeta, artefacto, "sin_json", escribir_json=False)
    with pytest.raises(ValueError, match="No se pudo leer"):
        rm.registrar_archivo(alm, sin_json)
    incompleto = guardar(carpeta, artefacto, "incompleto")
    incompleto.with_suffix(".json").write_text(json.dumps({"modelo": "gb"}), encoding="utf-8")
    with pytest.raises(ValueError, match="faltan datos: version, conjunto"):
        rm.registrar_archivo(alm, incompleto)
    roto = guardar(carpeta, artefacto, "roto")
    roto.with_suffix(".json").write_text("{no es json", encoding="utf-8")
    with pytest.raises(ValueError, match="No se pudo leer"):
        rm.registrar_archivo(alm, roto)


def test_adoptar_registra_el_modelo_versionado_identico_a_modelo_actual_y_se_puede_repetir(carpeta, alm, artefacto):  # noqa: F811
    versionado = guardar(carpeta, artefacto, "gb_completo_v0.9.0")
    sin = guardar(carpeta, artefacto, "gb_completo_sinestado_v0.9.0", sin_estado=True)
    (carpeta / "modelo_actual.joblib").write_bytes(versionado.read_bytes())
    (carpeta / "modelo_actual_sin_estado.joblib").write_bytes(sin.read_bytes())
    adoptados = rm.adoptar_actuales(alm)
    assert [(rol, archivo) for rol, _, archivo in adoptados] == [
        ("principal", "gb_completo_v0.9.0.joblib"),
        ("sin_estado", "gb_completo_sinestado_v0.9.0.joblib"),
    ]
    assert (
        alm.registro_modelos.en_uso("principal")["archivo"] == "gb_completo_v0.9.0.joblib"
    )  # se registra el VERSIONADO, no la copia sin nombre
    assert (
        rm.adoptar_actuales(alm) == adoptados and len(alm.registro_modelos.listar()) == 2
    )  # repetirlo no duplica nada


def test_adoptar_explica_cuando_el_actual_no_se_parece_a_ningun_versionado_o_no_hay_nada(carpeta, alm, artefacto):  # noqa: F811
    with pytest.raises(ValueError, match="No hay modelo_actual"):
        rm.adoptar_actuales(alm)
    (carpeta / "modelo_actual.joblib").write_bytes(b"un modelo que nadie versiono")
    guardar(carpeta, artefacto, "gb_v1")
    with pytest.raises(ValueError, match="no es idéntico a ningún modelo versionado"):
        rm.adoptar_actuales(alm)
    assert alm.registro_modelos.listar() == []  # y no se hizo nada a medias


# ------------------------------------------------------------------ la carga para el servicio
def registrado_y_en_uso(alm, carpeta, art, nombre="gb_completo", **kw):
    ruta = guardar(carpeta, art, nombre, **kw)
    id_ = rm.registrar_archivo(alm, ruta)
    alm.registro_modelos.promover(id_)
    return ruta, id_


def test_con_un_modelo_en_uso_se_carga_verificado_y_la_deserializacion_ocurre_sobre_los_bytes_ya_comprobados(
    carpeta, alm, artefacto, monkeypatch
):  # noqa: F811
    ruta, id_ = registrado_y_en_uso(alm, carpeta, artefacto)
    vistos = []
    cargar = joblib.load
    monkeypatch.setattr(
        rm.joblib, "load", lambda origen, *a, **k: (vistos.append(type(origen).__name__), cargar(origen, *a, **k))[1]
    )
    art, sin, info = rm.cargar_para_servicio(alm, Ajustes())
    assert art["meta"]["version"] == "0.9.0" and sin is None
    assert info["principal"] == {"id": id_, "huella": rm.sha256_de(ruta)[:12], "version": "0.9.0", "verificado": True}
    assert vistos == [
        "BytesIO"
    ]  # nunca desde la ruta: lo que se deserializa son exactamente los bytes cuya huella se comprobó


def test_con_la_huella_distinta_no_se_deserializa_nada_y_el_servicio_no_arranca(
    carpeta, alm, artefacto, monkeypatch, tmp_path
):  # noqa: F811
    ruta, _ = registrado_y_en_uso(alm, carpeta, artefacto)
    ruta.write_bytes(ruta.read_bytes() + b"manipulado")
    monkeypatch.setattr(
        rm.joblib, "load", lambda *a, **k: pytest.fail("joblib.load NO debía ejecutarse con un archivo alterado")
    )
    with pytest.raises(ModeloAlterado):
        rm.cargar_para_servicio(alm, Ajustes())
    ajustes = Ajustes(ruta_bd=alm.base.ruta, pepper=PEPPER)
    with pytest.raises(ModeloAlterado, match="NO coincide"):
        crear_app(ajustes)  # el servicio ni siquiera arranca


def test_un_modelo_en_uso_que_desaparecio_tampoco_arranca(carpeta, alm, artefacto):  # noqa: F811
    ruta, _ = registrado_y_en_uso(alm, carpeta, artefacto)
    ruta.unlink()
    with pytest.raises(ModeloAlterado, match="No se pudo leer"):
        rm.cargar_para_servicio(alm, Ajustes())


def test_sin_registro_se_usa_el_modelo_de_siempre_con_un_aviso_salvo_que_se_exija_el_registro(
    carpeta, alm, artefacto, caplog
):  # noqa: F811
    (carpeta / "modelo_actual.joblib").write_bytes(guardar(carpeta, artefacto, "gb_v1").read_bytes())
    with caplog.at_level(logging.WARNING, logger="modelo_ia.modelos"):
        art, sin, info = rm.cargar_para_servicio(alm, Ajustes())
    assert (
        art["meta"]["version"] == "0.9.0"
        and info["principal"]["verificado"] is False
        and "SIN verificar" in caplog.text
    )
    with pytest.raises(RegistroIncompleto, match="No hay un modelo EN USO"):
        rm.cargar_para_servicio(alm, Ajustes(exigir_registro_modelos=True))


def test_con_registro_el_modelo_sin_estado_no_registrado_no_se_carga_nunca_exijas_o_no_el_registro(
    carpeta, alm, artefacto, monkeypatch, caplog
):
    """El hueco que encontró la revisión: con un principal registrado, el sin_estado se cargaba del archivo de siempre SIN verificar."""
    registrado_y_en_uso(alm, carpeta, artefacto)
    (carpeta / "modelo_actual_sin_estado.joblib").write_bytes(
        guardar(carpeta, artefacto, "sin_v1", sin_estado=True, version="0.8.0").read_bytes()
    )
    monkeypatch.setattr(
        rm.predecir,
        "cargar_artefacto_sin_estado",
        lambda: pytest.fail("no debe deserializarse un modelo sin_estado que no está en el registro"),
    )
    for exigir in (True, False):
        with caplog.at_level(logging.WARNING, logger="modelo_ia.modelos"):
            caplog.clear()
            art, sin, info = rm.cargar_para_servicio(alm, Ajustes(exigir_registro_modelos=exigir))
        assert art["meta"]["version"] == "0.9.0" and sin is None and info["sin_estado"]["verificado"] is False, exigir
        assert "se desactiva esa variante" in caplog.text


def test_con_registro_un_principal_sin_registrar_es_un_error_y_no_se_carga_el_de_siempre(
    carpeta, alm, artefacto, monkeypatch
):
    """El caso inverso: solo el sin_estado está registrado. El principal NO se carga de modelo_actual.joblib sin verificar."""
    registrado_y_en_uso(alm, carpeta, artefacto, "sin_v1", sin_estado=True)
    (carpeta / "modelo_actual.joblib").write_bytes(guardar(carpeta, artefacto, "pri_v1", version="0.8.0").read_bytes())
    monkeypatch.setattr(
        rm.predecir,
        "cargar_artefacto",
        lambda *a, **k: pytest.fail("no debe deserializarse un principal que no está en el registro"),
    )
    for exigir in (True, False):
        with pytest.raises(RegistroIncompleto, match="ninguno para el rol «principal»"):
            rm.cargar_para_servicio(alm, Ajustes(exigir_registro_modelos=exigir))


def test_un_modelo_elegido_a_mano_se_rechaza_cuando_el_registro_ya_tiene_modelos_en_uso(
    carpeta, alm, artefacto, monkeypatch
):
    a_mano = guardar(carpeta, artefacto, "a_mano")
    registrado_y_en_uso(alm, carpeta, artefacto, "registrado", version="1.0.0")
    monkeypatch.setattr(rm.predecir, "cargar_artefacto", lambda *a, **k: pytest.fail("no debe cargarse"))
    with pytest.raises(RegistroIncompleto, match="ya hay modelos en uso en el registro"):
        rm.cargar_para_servicio(alm, Ajustes(ruta_modelo=a_mano))
    with pytest.raises(RegistroIncompleto, match="Quita MODELO_IA_MODELO"):
        crear_app(
            Ajustes(ruta_bd=alm.base.ruta, pepper=PEPPER, ruta_modelo=a_mano)
        )  # y el servicio tampoco arranca así


def test_los_datos_registrados_deben_coincidir_con_los_del_propio_modelo(carpeta, alm, artefacto):
    """El .json con que se registra no está protegido por la huella: un .json que no corresponda al modelo se detecta al cargar."""
    ruta = guardar(carpeta, artefacto, "gb_v1", version="0.9.0")
    id_ = alm.registro_modelos.registrar(
        ruta.name, rm.sha256_de(ruta), "otro_modelo", "9.9.9", "completo", "principal"
    )  # huella buena, datos que no son
    alm.registro_modelos.promover(id_)
    with pytest.raises(ModeloAlterado, match="no coinciden con los que se registraron"):
        rm.cargar_para_servicio(alm, Ajustes())


def test_adoptar_no_reemplaza_un_modelo_que_ya_esta_en_uso_y_es_otro(carpeta, alm, artefacto):
    viejo = guardar(carpeta, artefacto, "gb_v1", version="1.0.0")
    (carpeta / "modelo_actual.joblib").write_bytes(viejo.read_bytes())
    rm.adoptar_actuales(alm)
    nuevo = rm.registrar_archivo(alm, guardar(carpeta, artefacto, "gb_v2", version="2.0.0"))
    alm.registro_modelos.promover(nuevo)  # alguien promovió un modelo nuevo a mano
    with pytest.raises(ValueError, match="Ya hay un modelo en uso para el rol «principal»"):
        rm.adoptar_actuales(alm)  # repetir «adoptar» no debe hacer retroceder al modelo viejo
    assert alm.registro_modelos.en_uso("principal")["id"] == nuevo


def test_un_modelo_elegido_a_mano_se_salta_el_registro_salvo_que_se_exija(carpeta, alm, artefacto, caplog):  # noqa: F811
    a_mano = guardar(carpeta, artefacto, "a_mano")
    with caplog.at_level(logging.WARNING, logger="modelo_ia.modelos"):
        art, _, info = rm.cargar_para_servicio(alm, Ajustes(ruta_modelo=a_mano))
    assert (
        art["meta"]["version"] == "0.9.0"
        and info["principal"]["verificado"] is False
        and "MODELO_IA_MODELO" in caplog.text
    )
    with pytest.raises(RegistroIncompleto, match="se salta el registro"):
        rm.cargar_para_servicio(alm, Ajustes(ruta_modelo=a_mano, exigir_registro_modelos=True))


def test_un_modelo_dado_a_crear_app_se_respeta_sin_tocar_el_registro(alm, artefacto):  # noqa: F811
    art, sin, info = rm.preparar(alm, Ajustes(exigir_registro_modelos=True), artefacto, None)
    assert art is artefacto and sin is None and info["principal"]["verificado"] is False


# ------------------------------------------------------------------ deriva: números conocidos
def poner(alm, art, puntaje, creada=None, version=None, conjunto=None, estado_origen="comercio", comercio=None):
    """Guarda una evaluación del modelo de las pruebas con ese puntaje (None = modo degradado, sin puntaje)."""
    n = next(poner.n)
    creada = creada or time.strftime("%Y-%m-%dT%H:%M:%S")
    v = dict(
        id=f"{n:08d}-0000-4000-8000-000000000000",
        comercio_id=comercio or poner.comercio,
        id_transaccion=f"T{n}",
        id_cliente=f"C{n}",
        fecha=creada,
        huella=None,
        fila_json="{}",
        solicitud_hash=f"h{n}",
        decision="APROBADA",
        decision_modelo="APROBADA",
        probabilidad=puntaje,
        motivos_json="[]",
        reglas_json="[]",
        modelo_nombre=art["meta"]["modelo"],
        modelo_version=version or art["meta"]["version"],
        modelo_conjunto=conjunto or art["meta"]["conjunto"],
        modo="normal" if puntaje is not None else "degradado",
        latencia_ms=1.0,
        creada=creada,
        red_origen="comercio",
        estado_origen=estado_origen,
        id_seguimiento=f"AF-{n:04d}-ABCD",
        ip_codigo=None,
        disp_codigo=None,
        monto_cop=1000,
        no_enviados_json="[]",
        senales_json=None,
    )
    with alm.transaccion() as con:
        alm.evaluaciones.insertar(con, **v)


poner.n = iter(range(1, 10**7))


def con_referencia(ctx, referencia=REF, rol="principal"):
    """Deja en el registro un modelo EN USO con esa referencia (no hace falta que exista el archivo: solo se lee la referencia)."""
    r = ctx.app.state.almacen.registro_modelos
    r.promover(
        r.registrar(
            f"f{next(poner.n)}.joblib", f"{next(poner.n):064d}", "logistica", "0.9.0", "completo", rol, referencia
        )
    )


def deriva_de(ctx, quien="analista", **params):
    return ctx.cliente.get("/v1/panel/deriva", params=params, headers=ctx.h(quien))


@pytest.fixture
def ctx(tmp_path, artefacto):  # noqa: F811
    c = construir_contexto(tmp_path / "d", artefacto)
    poner.comercio = c.comercios["A"]
    return c


def distribuir(ctx, art, proporciones, total, **kw):
    """Inserta `total` pagos repartidos como `proporciones` (cada puntaje cae en el centro de su cubeta)."""
    alm = ctx.app.state.almacen
    for i, p in enumerate(proporciones):
        for _ in range(round(p * total)):
            poner(alm, art, i / 10 + 0.05, **kw)


def test_el_psi_del_endpoint_coincide_con_el_calculo_a_mano_y_el_estado_con_el_umbral(ctx, artefacto):  # noqa: F811
    con_referencia(ctx)
    ctx.app.state.ajustes.deriva_min_muestra = 100
    esperada = [0.30, 0.25, 0.15, 0.10, 0.05, 0.05, 0.04, 0.03, 0.02, 0.01]  # una población parecida pero no igual
    distribuir(ctx, artefacto, esperada, 1000)
    r = deriva_de(ctx).json()
    a_mano = sum((o - e) * __import__("math").log(o / e) for e, o in zip(REF["proporciones"], esperada))
    assert r["psi"] == pytest.approx(a_mano, abs=1e-6) and r["ventana"]["pagos"] == 1000
    assert r["estado"] == ("atencion" if 0.10 <= a_mano < 0.25 else "alerta" if a_mano >= 0.25 else "estable")
    assert [c["referencia"] for c in r["cubetas"]] == REF["proporciones"] and sum(
        c["observada"] for c in r["cubetas"]
    ) == pytest.approx(1.0)
    assert r["modelo"] == {
        "nombre": "logistica",
        "version": r["modelo"]["version"],
        "conjunto": "completo",
        "huella_verificada": False,
    }
    assert r["referencia"] == {"disponible": True, "pagos": 1000} and r["umbrales"] == {"atencion": 0.1, "alerta": 0.25}


@pytest.mark.parametrize(
    "observada,esperado",
    [
        (REF["proporciones"], "estable"),
        ([0.35, 0.20, 0.12, 0.09, 0.07, 0.06, 0.04, 0.03, 0.02, 0.02], "atencion"),  # PSI 0,129
        ([0.05, 0.05, 0.05, 0.05, 0.10, 0.10, 0.15, 0.15, 0.15, 0.15], "alerta"),
    ],
)
def test_cada_estado_aparece_con_su_poblacion(ctx, artefacto, observada, esperado):  # noqa: F811
    con_referencia(ctx)
    ctx.app.state.ajustes.deriva_min_muestra = 100
    distribuir(ctx, artefacto, observada, 1000)
    r = deriva_de(ctx).json()
    assert r["estado"] == esperado, r["psi"]


def test_sin_referencia_sin_pagos_y_muestra_insuficiente_no_dan_alarma(ctx, artefacto):  # noqa: F811
    r = deriva_de(ctx).json()
    assert (
        r["estado"] == "sin_referencia"
        and r["psi"] is None
        and r["referencia"] == {"disponible": False, "pagos": None}
        and "modelo-referencia" in r["nota"]
    )
    con_referencia(ctx)
    r = deriva_de(ctx).json()
    assert r["estado"] == "sin_pagos" and r["psi"] is None and r["ventana"]["pagos"] == 0
    distribuir(ctx, artefacto, [0.0] * 9 + [1.0], 499)  # todo en la última cubeta: un PSI enorme, pero con 499 pagos
    r = deriva_de(ctx).json()
    assert (
        r["estado"] == "muestra_insuficiente" and r["psi"] > 1.0 and "menos de 500" in r["nota"]
    )  # se informa el valor, pero NO se da alerta
    poner(ctx.app.state.almacen, artefacto, 0.95)
    assert deriva_de(ctx).json()["estado"] == "alerta"  # con la muestra mínima, sí


def test_solo_cuentan_los_pagos_del_modelo_en_uso_dentro_de_la_ventana_con_puntaje_y_con_estado(ctx, artefacto):  # noqa: F811
    con_referencia(ctx)
    ctx.app.state.ajustes.deriva_min_muestra = 10
    alm = ctx.app.state.almacen
    for _ in range(12):
        poner(alm, artefacto, 0.05)  # cuentan
    for _ in range(40):
        poner(alm, artefacto, 0.95, creada="2020-01-01T10:00:00")  # muy viejos: fuera de la ventana de 7 días
    for _ in range(40):
        poner(alm, artefacto, 0.95, version="0.0.1")  # de otra versión del modelo
    for _ in range(40):
        poner(alm, artefacto, 0.95, conjunto="historial")  # de otro conjunto de variables
    for _ in range(40):
        poner(alm, artefacto, None)  # modo degradado: no tienen puntaje
    for _ in range(40):
        poner(alm, artefacto, 0.95, estado_origen="no_enviado")  # los que usaron la variante sin estado del dispositivo
    r = deriva_de(ctx).json()
    assert r["ventana"]["pagos"] == 12 and [c["observada"] for c in r["cubetas"]][0] == 1.0
    assert deriva_de(ctx, ventana_dias=90).json()["ventana"]["pagos"] == 12  # (los de 2020 siguen muy lejos)


def test_la_ventana_se_valida_y_se_respeta(ctx, artefacto):  # noqa: F811
    con_referencia(ctx)
    alm = ctx.app.state.almacen
    ayer = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    for _ in range(5):
        poner(alm, artefacto, 0.45, creada=ayer)
    assert deriva_de(ctx, ventana_dias=1).json()["ventana"]["pagos"] == 0
    assert deriva_de(ctx, ventana_dias=7).json()["ventana"]["pagos"] == 5
    for malo in ("0", "91", "-1", "abc", "1.5"):
        assert deriva_de(ctx, ventana_dias=malo).status_code == 422, malo


def test_la_referencia_del_registro_manda_sobre_la_del_propio_modelo_y_esta_sirve_si_no_hay_registro(ctx):
    otra = {"n": 7, "proporciones": [0.1] * 10}
    servicio = ctx.app.state.servicio
    # una COPIA de los datos del modelo con la referencia que trae el propio modelo (el de la sesión es compartido: no se muta)
    servicio.meta = dict(servicio.meta, referencia_puntaje=otra)
    assert deriva_de(ctx).json()["referencia"] == {"disponible": True, "pagos": 7}  # sin registro: se usa esa
    con_referencia(ctx)
    assert deriva_de(ctx).json()["referencia"] == {
        "disponible": True,
        "pagos": 1000,
    }  # con registro: manda la registrada
    mala = {"n": 3, "proporciones": [0.5, 0.5]}  # una referencia mal formada (no son 10 cubetas) no rompe nada
    servicio.meta = dict(servicio.meta, referencia_puntaje=mala)
    r = ctx.app.state.almacen.registro_modelos
    r.guardar_referencia(r.en_uso("principal")["id"], mala)
    assert deriva_de(ctx).json()["estado"] == "sin_referencia"


def test_la_deriva_y_los_modelos_son_solo_del_personal(ctx):
    for ruta in ("/v1/panel/deriva", "/v1/panel/modelos"):
        assert ctx.cliente.get(ruta).status_code == 401, ruta
        assert ctx.cliente.get(ruta, headers={"X-API-Key": "falsa"}).status_code == 401, ruta
        for quien in ("A", "B"):
            assert ctx.cliente.get(ruta, headers=ctx.h(quien)).status_code == 403, (ruta, quien)
        for quien in ("analista", "admin"):
            assert ctx.cliente.get(ruta, headers=ctx.h(quien)).status_code == 200, (ruta, quien)


def test_los_modelos_registrados_muestran_12_caracteres_de_la_huella_y_cual_se_ejecuta(carpeta, tmp_path, artefacto):  # noqa: F811
    alm = Almacen(tmp_path / "e.sqlite3", PEPPER)
    alm.inicializar()
    ruta, id_ = registrado_y_en_uso(alm, carpeta, artefacto, "gb_completo")
    otro = rm.registrar_archivo(alm, guardar(carpeta, artefacto, "gb_completo_v1", version="1.0.0"))
    app = crear_app(
        Ajustes(ruta_bd=alm.base.ruta, pepper=PEPPER), artefacto_sin_estado=None
    )  # sin pasar el modelo: lo carga el registro, verificado
    clave = alm.crear_clave(1, "analista")
    from fastapi.testclient import TestClient

    filas = {f["id"]: f for f in TestClient(app).get("/v1/panel/modelos", headers={"X-API-Key": clave}).json()}
    assert (
        filas[id_]["estado"] == "en_uso"
        and filas[id_]["en_ejecucion"] is True
        and filas[id_]["huella"] == rm.sha256_de(ruta)[:12]
    )
    assert filas[otro]["estado"] == "candidato" and filas[otro]["en_ejecucion"] is False
    assert all(len(f["huella"]) == 12 for f in filas.values()) and rm.sha256_de(ruta) not in json.dumps(
        list(filas.values())
    )
    r = TestClient(app).get("/v1/panel/deriva", headers={"X-API-Key": clave}).json()
    assert (
        r["modelo"]["huella_verificada"] is True
    )  # el endpoint de deriva dice que el modelo que mide es el verificado


def test_las_cubetas_de_sql_y_las_de_python_son_las_mismas_incluso_en_los_bordes(ctx, artefacto):  # noqa: F811
    alm = ctx.app.state.almacen
    valores = [
        0.0,
        0.1,
        0.2,
        0.3,
        0.7,
        0.9,
        0.0999999999,
        0.29999999999999999,
        0.30000000000000004,
        0.7000000000000001,
        0.5,
        0.9999999,
        1.0,
        0.05,
        0.15,
        0.25,
        0.35,
        0.65,
    ]
    for v in valores:
        poner(alm, artefacto, v)
    with alm.lectura() as con:
        sql = alm.consultas.distribucion_puntaje(
            con, "logistica", artefacto["meta"]["version"], "completo", "2000-01-01T00:00:00"
        )
    assert sql == deriva.histograma(valores)


def test_las_metricas_de_deriva_salen_con_los_pagos_y_el_psi_y_si_fallan_el_resto_se_entrega(
    ctx, artefacto, monkeypatch
):  # noqa: F811
    con_referencia(ctx)
    distribuir(ctx, artefacto, REF["proporciones"], 600)
    texto = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text
    pagos = re.search(r"^antifraude_deriva_pagos (\d+)$", texto, re.M)
    psi = re.search(r"^antifraude_deriva_psi ([0-9.]+)$", texto, re.M)
    assert pagos and int(pagos.group(1)) == 600 and psi and float(psi.group(1)) < 0.01
    assert "# TYPE antifraude_deriva_psi gauge" in texto and "antifraude_http_peticiones_total" in texto
    monkeypatch.setattr(type(ctx.app.state.consultas), "lineas_deriva_prometheus", lambda *a, **k: 1 / 0)
    r = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin"))
    assert (
        r.status_code == 200 and "antifraude_deriva" not in r.text and "antifraude_http_peticiones_total" in r.text
    )  # las demás métricas se entregan igual


# ------------------------------------------------------------------ entrenar guarda la referencia
def test_entrenar_guarda_la_distribucion_de_referencia_en_el_resultado_y_en_el_json(tmp_path, monkeypatch):
    from modelo_ia import entrenar
    from modelo_ia.tests.conftest import hacer_datos_completos

    ruta = tmp_path / "completo.csv"
    hacer_datos_completos().to_csv(ruta, index=False, encoding="utf-8-sig")
    df, mascaras = entrenar.preparar_datos(ruta, "completo")
    r = entrenar.ajustar_y_evaluar(df, mascaras, "logistica", "completo")
    ref = r["referencia_puntaje"]
    assert (
        ref["n"] == int(mascaras["validacion"].sum()) == r["filas"]["validacion"]
        and len(ref["proporciones"]) == 10
        and sum(ref["proporciones"]) == pytest.approx(1.0)
    )
    monkeypatch.setattr(config, "CARPETA_MODELOS", tmp_path / "m")
    guardada = entrenar.guardar(r, ruta)
    meta = json.loads(guardada.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["referencia_puntaje"] == ref and joblib.load(guardada)["meta"]["referencia_puntaje"] == ref


def test_calcular_referencia_de_un_modelo_ya_entrenado_puntua_su_validacion(carpeta, alm, tmp_path, artefacto):  # noqa: F811
    from modelo_ia import entrenar, modelos
    from modelo_ia.tests.conftest import hacer_datos_completos

    datos = tmp_path / "completo.csv"
    hacer_datos_completos().to_csv(datos, index=False, encoding="utf-8-sig")
    art = dict(
        artefacto,
        meta=dict(
            artefacto["meta"], numericas=artefacto["meta"]["numericas"], categoricas=artefacto["meta"]["categoricas"]
        ),
    )
    ruta = guardar(carpeta, art, "gb_completo")
    id_ = rm.registrar_archivo(alm, ruta)
    assert alm.registro_modelos.por_id(id_)["referencia"] is None
    ref = rm.calcular_referencia(alm, id_, datos)
    df, mascaras = entrenar.preparar_datos(datos, "completo")
    columnas = art["meta"]["numericas"] + art["meta"]["categoricas"]
    p = modelos.puntajes(art["pipeline"], df.loc[mascaras["validacion"], columnas])
    assert ref == {"n": int(len(p)), "proporciones": deriva.proporciones(deriva.histograma(p.tolist()))}
    assert alm.registro_modelos.por_id(id_)["referencia"] == ref
    with pytest.raises(ValueError, match="No existe"):
        rm.calcular_referencia(alm, 999, datos)
    ruta.write_bytes(ruta.read_bytes() + b"x")
    with pytest.raises(ModeloAlterado):  # ni para calcular la referencia se deserializa un archivo alterado
        rm.calcular_referencia(alm, id_, datos)


# ------------------------------------------------------------------ la consola
@pytest.fixture
def consola(tmp_path, monkeypatch, carpeta):
    ruta = tmp_path / "consola.sqlite3"
    monkeypatch.setenv("MODELO_IA_BD", str(ruta))
    monkeypatch.setenv("MODELO_IA_PEPPER", PEPPER)
    a = Almacen(ruta, PEPPER)
    a.inicializar()
    return a


def correr(capsys, *args):
    admin.main(list(args))
    return capsys.readouterr().out


def test_la_consola_registra_promueve_revierte_y_verifica(consola, carpeta, artefacto, capsys):  # noqa: F811
    guardar(carpeta, artefacto, "gb_v1", version="1.0.0")
    guardar(carpeta, artefacto, "gb_v2", version="2.0.0")
    assert "(no hay modelos registrados" in correr(capsys, "modelo-listar")
    assert "número 1 (candidato)" in correr(capsys, "modelo-registrar", "--archivo", "gb_v1.joblib")
    assert "número 2 (candidato)" in correr(
        capsys, "modelo-registrar", "--archivo", str(carpeta / "gb_v2.joblib")
    )  # también acepta la ruta
    assert "EN USO" in correr(capsys, "modelo-promover", "--id", "1") and "EN USO" in correr(
        capsys, "modelo-promover", "--id", "2"
    )
    salida = correr(capsys, "modelo-listar")
    assert re.search(r"\s1\s+principal\s+archivado", salida) and re.search(r"\s2\s+principal\s+en_uso", salida)
    assert "Vuelto al modelo 1" in correr(capsys, "modelo-revertir", "--rol", "principal")
    assert "gb_v1.joblib: ok" in correr(capsys, "modelo-verificar")
    (carpeta / "gb_v1.joblib").write_bytes(b"alterado")
    with pytest.raises(SystemExit, match="NO coincide con su huella"):
        admin.main(["modelo-verificar"])
    assert "NO coincide con el que se registró" in capsys.readouterr().out


def test_la_consola_explica_los_errores_y_adopta_lo_que_ya_se_usaba(consola, carpeta, artefacto, capsys):  # noqa: F811
    with pytest.raises(SystemExit, match="No existe"):
        admin.main(["modelo-registrar", "--archivo", "no_esta.joblib"])
    with pytest.raises(SystemExit, match="No existe un modelo registrado"):
        admin.main(["modelo-promover", "--id", "99"])
    with pytest.raises(SystemExit, match="No hay un modelo anterior"):
        admin.main(["modelo-revertir"])
    assert "No hay modelos en uso" in correr(capsys, "modelo-verificar")
    versionado = guardar(carpeta, artefacto, "gb_completo_v0.9.0")
    (carpeta / "modelo_actual.joblib").write_bytes(versionado.read_bytes())
    assert "registrado y EN USO" in correr(capsys, "modelo-adoptar")
    assert consola.registro_modelos.en_uso("principal")["archivo"] == "gb_completo_v0.9.0.joblib"


# ------------------------------------------------------------------ la migración 3
def test_la_migracion_3_crea_el_registro_conserva_los_datos_y_hace_una_sola_copia(tmp_path):
    ruta = tmp_path / "v2.sqlite3"
    v2 = Almacen(ruta, PEPPER)
    assert v2.inicializar(v2._migraciones()[:2]) == [1, 2]  # una base como la de la versión anterior
    clave = v2.crear_clave(1, "admin")
    assert not sqlite3.connect(ruta).execute("SELECT 1 FROM sqlite_master WHERE name='modelos_registro'").fetchone()
    nuevo = Almacen(ruta, PEPPER)
    assert nuevo.inicializar() == [3, 4, 5]
    con = sqlite3.connect(ruta)
    assert con.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('modelos_registro','un_modelo_en_uso_por_rol') ORDER BY name"
    ).fetchall() == [("modelos_registro",), ("un_modelo_en_uso_por_rol",)]
    assert nuevo.autenticar(clave) is not None and nuevo.registro_modelos.listar() == []
    copias = nuevo.copias_previas()
    assert len(copias) == 1 and "-v3-" in copias[0].name
    assert (
        not sqlite3.connect(copias[0]).execute("SELECT 1 FROM sqlite_master WHERE name='modelos_registro'").fetchone()
    )  # la copia es la de ANTES
    assert nuevo.inicializar() == []


class FalsoRegistroModelos:
    """Cumple la interfaz `ModelosRegistroRepo` (una prueba lo comprueba): para probar lo que usa el registro sin base de datos."""

    def __init__(self):
        self.filas = []

    def registrar(self, archivo, sha256, nombre, version, conjunto, rol, referencia=None):
        self.filas.append(
            {
                "id": len(self.filas) + 1,
                "archivo": archivo,
                "sha256": sha256,
                "nombre": nombre,
                "version": version,
                "conjunto": conjunto,
                "rol": rol,
                "estado": "candidato",
                "referencia": referencia,
            }
        )
        return len(self.filas)

    def listar(self):
        return list(self.filas)

    def por_id(self, id_):
        return next((f for f in self.filas if f["id"] == id_), None)

    def en_uso(self, rol):
        return next((f for f in self.filas if f["rol"] == rol and f["estado"] == "en_uso"), None)

    def promover(self, id_):
        f = self.por_id(id_)
        f["estado"] = "en_uso"
        return f

    def revertir(self, rol):
        raise ValueError("sin historia")

    def guardar_referencia(self, id_, referencia):
        self.por_id(id_)["referencia"] = referencia


def test_la_carga_verificada_funciona_con_un_registro_falso_sin_base_de_datos(carpeta, artefacto):
    """El registro de modelos solo necesita la interfaz: la verificación y la carga se prueban sin SQLite."""
    from types import SimpleNamespace

    ruta = guardar(carpeta, artefacto, "gb_falso")
    falso = SimpleNamespace(registro_modelos=FalsoRegistroModelos())
    id_ = rm.registrar_archivo(falso, ruta)
    falso.registro_modelos.promover(id_)
    art, _, info = rm.cargar_para_servicio(falso, Ajustes())
    assert art["meta"]["version"] == "0.9.0" and info["principal"]["verificado"] is True
    ruta.write_bytes(b"cambiado")
    with pytest.raises(ModeloAlterado):
        rm.cargar_para_servicio(falso, Ajustes())


def test_revertir_vuelve_al_modelo_que_estaba_en_uso_justo_antes_aunque_haya_varios_archivados(alm):
    """Con dos o más archivados, «el anterior» es el último que estuvo en uso (el promovido más recientemente), no el más viejo."""
    r = alm.registro_modelos
    ids = [r.registrar(f"m{i}.joblib", str(i) * 64, "gb", f"{i}.0.0", "completo", "principal") for i in range(1, 5)]
    for id_ in ids[:3]:
        r.promover(id_)  # 1, luego 2, luego 3 en uso: quedan archivados 1 y 2
    estados = {m["id"]: m["estado"] for m in r.listar()}
    assert estados == {ids[0]: "archivado", ids[1]: "archivado", ids[2]: "en_uso", ids[3]: "candidato"}
    assert r.revertir("principal")["id"] == ids[1]  # vuelve a 2 (el de justo antes), NO a 1
    assert r.en_uso("principal")["id"] == ids[1] and sum(m["estado"] == "en_uso" for m in r.listar()) == 1


def test_la_deriva_usa_la_referencia_del_modelo_que_corre_aunque_se_promueva_otro_antes_de_reiniciar(ctx):
    r = ctx.app.state.almacen.registro_modelos
    corriendo = r.registrar(
        "a.joblib", "a" * 64, "logistica", "0.9.0", "completo", "principal", REF
    )  # referencia de 1000 pagos
    r.promover(corriendo)
    ctx.app.state.info_modelos = {
        "principal": {"id": corriendo, "huella": "a" * 12, "version": "0.9.0", "verificado": True}
    }  # lo que verificó el arranque
    nuevo = r.registrar(
        "b.joblib", "b" * 64, "logistica", "1.0.0", "completo", "principal", {"n": 7, "proporciones": [0.1] * 10}
    )
    r.promover(nuevo)  # se promovió otro, pero el servicio sigue ejecutando el primero
    resp = deriva_de(ctx).json()
    assert resp["referencia"] == {"disponible": True, "pagos": 1000} and resp["modelo"]["huella_verificada"] is True
    assert "antifraude_deriva" in ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text


@pytest.mark.solo_sqlite
def test_dos_procesos_reales_promoviendo_a_la_vez_nunca_dejan_dos_modelos_en_uso(tmp_path):
    """Con hilos del mismo proceso el candado de Python ya ordena todo; aquí son PROCESOS distintos: lo único que los ordena es SQLite."""
    import subprocess
    import sys

    raiz = Path(__file__).resolve().parents[2]
    codigo = "import sys\nfrom modelo_ia.api.almacen import Almacen\na = Almacen(sys.argv[1], 'pepper-de-prueba')\ntry:\n    a.registro_modelos.promover(int(sys.argv[2]))\nexcept ValueError:\n    pass\n"
    env = {**__import__("os").environ, "PYTHONPATH": str(raiz)}
    for ronda in range(15):
        ruta = tmp_path / f"p{ronda}.sqlite3"
        base = Almacen(ruta, PEPPER)
        base.inicializar()
        ids = [
            base.registro_modelos.registrar(f"m{i}.joblib", str(i) * 64, "gb", f"{i}", "c", "principal")
            for i in range(1, 4)
        ]
        hijos = [
            subprocess.Popen([sys.executable, "-c", codigo, str(ruta), str(i)], env=env, cwd=str(raiz)) for i in ids
        ]
        assert [h.wait(timeout=120) for h in hijos] == [0, 0, 0]
        estados = sorted(m["estado"] for m in base.registro_modelos.listar())
        assert estados == ["archivado", "archivado", "en_uso"], (
            ronda,
            estados,
        )  # los tres se promovieron en algún orden: uno en uso y dos archivados
