"""El segundo factor (TOTP) del administrador: cuándo se exige, que un código no se repita, el bloqueo, la concurrencia, la consola, la migración 2 y que
ningún secreto ni código aparezca en registros, métricas, errores o auditoría."""

import io
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from modelo_ia.api import admin, totp
from modelo_ia.api import observabilidad as ob
from modelo_ia.api.almacen import Almacen
from modelo_ia.tests.test_api import RELOJ, artefacto, construir_contexto  # noqa: F401  (artefacto es un fixture)

T0 = RELOJ.timestamp()
PEPPER = "pepper-de-prueba"


@pytest.fixture
def ctx(tmp_path, artefacto):  # noqa: F811
    return construir_contexto(tmp_path / "m", artefacto, exigir_mfa=True)


def poner_hora(ctx, epoch):
    ctx.app.state.servicio.reloj = lambda: datetime.fromtimestamp(epoch)


def activar(ctx, epoch=T0):
    """Deja el segundo factor del administrador del contexto ACTIVO y devuelve (id de la clave, secreto)."""
    alm = ctx.app.state.almacen
    clave_id = alm.autenticar(ctx.claves["admin"]).clave_id
    secreto = alm.mfa_iniciar(clave_id)
    assert alm.mfa_confirmar(clave_id, totp.codigo(secreto, epoch), epoch)
    return clave_id, secreto


def cabeceras(ctx, codigo=None, quien="admin"):
    return {**ctx.h(quien), **({"X-TOTP": codigo} if codigo is not None else {})}


_n = iter(range(1, 100_000))


def aparece(texto, valor):
    """Un valor «aparece» como palabra completa: un código de 6 dígitos suelto dentro de otro número o de un UUID no cuenta (evita falsos positivos por azar)."""
    return re.search(r"(?<![0-9A-Za-z])" + re.escape(valor) + r"(?![0-9A-Za-z])", texto) is not None


def crear(ctx, codigo=None, quien="admin", cliente=None):
    return (cliente or ctx.cliente).post(
        "/v1/comercios", json={"nombre": f"Tienda MFA {next(_n)}"}, headers=cabeceras(ctx, codigo, quien)
    )


def ejecutar(ctx, nombre, codigo):
    """UNA de las 4 acciones de administrador que cambian algo (cada una con su propio comercio, para no chocar entre sí)."""
    alm = ctx.app.state.almacen
    h, c = cabeceras(ctx, codigo), ctx.cliente
    if nombre == "crear":
        return c.post("/v1/comercios", json={"nombre": f"Tienda MFA {next(_n)}"}, headers=h)
    spare = alm.crear_comercio(f"Spare {next(_n)}")
    if nombre == "suspender":
        return c.post(f"/v1/comercios/{spare}/suspender", json={"motivo": "motivo de prueba"}, headers=h)
    if nombre == "reactivar":
        return c.post(f"/v1/comercios/{spare}/reactivar", headers=h)
    return c.put(
        f"/v1/comercios/{spare}/acuerdo",
        json={"version": "v1.0", "firmado_en": "2026-11-01", "referencia": "acuerdo.pdf"},
        headers=h,
    )


ACCIONES = ("crear", "suspender", "reactivar", "acuerdo")


def las_cuatro(ctx, codigo):
    """Las 4 acciones, una tras otra, con el mismo código (sirve cuando no se mira el segundo factor o se quiere ver que las 4 lo piden)."""
    return {nombre: ejecutar(ctx, nombre, codigo) for nombre in ACCIONES}


def exitosa(r):
    return r.status_code in (200, 201)


# ------------------------------------------------------------------ cuándo se exige
def test_con_la_exigencia_apagada_las_acciones_de_admin_funcionan_solo_con_la_clave(tmp_path, artefacto):  # noqa: F811
    ctx = construir_contexto(tmp_path / "off", artefacto)  # exigir_mfa por defecto: apagado
    assert not ctx.app.state.ajustes.exigir_mfa
    assert {k: exitosa(r) for k, r in las_cuatro(ctx, None).items()} == dict.fromkeys(
        ("crear", "suspender", "reactivar", "acuerdo"), True
    )
    clave_id, _ = activar(ctx)  # aunque el admin ya tenga segundo factor, con la exigencia apagada no se pide
    assert exitosa(crear(ctx))


def test_con_la_exigencia_activa_y_sin_segundo_factor_las_4_acciones_dan_403_pero_las_lecturas_siguen(ctx):
    for nombre, r in las_cuatro(ctx, None).items():
        assert r.status_code == 403 and r.json()["title"] == "Segundo factor no configurado", nombre
    for ruta in (
        "/v1/comercios",
        "/v1/panel/resumen",
        "/v1/panel/transacciones",
        "/v1/metricas",
        "/v1/yo",
    ):  # las lecturas del administrador: solo la clave
        assert ctx.cliente.get(ruta, headers=ctx.h("admin")).status_code == 200, ruta


def test_un_segundo_factor_pendiente_de_confirmar_todavia_no_cuenta(ctx):
    alm = ctx.app.state.almacen
    clave_id = alm.autenticar(ctx.claves["admin"]).clave_id
    secreto = alm.mfa_iniciar(clave_id)
    assert alm.mfa_estado(clave_id) == "pendiente"
    assert (
        crear(ctx, totp.codigo(secreto, T0)).status_code == 403
    )  # un código válido de un secreto sin confirmar no sirve
    assert not alm.mfa_confirmar(clave_id, "000000", T0) and alm.mfa_estado(clave_id) == "pendiente"
    assert alm.mfa_confirmar(clave_id, totp.codigo(secreto, T0), T0) and alm.mfa_estado(clave_id) == "activa"


def test_un_comercio_y_un_analista_reciben_403_sin_pistas_del_segundo_factor(ctx):
    _, secreto = activar(ctx)
    codigo = totp.codigo(secreto, T0)
    for quien in ("A", "B", "analista"):
        for c in (None, codigo, "000000", "x" * 500):
            r = crear(ctx, c, quien)
            assert r.status_code == 403 and r.json()["title"] == "Sin permiso", (quien, c)
    assert ctx.cliente.post("/v1/comercios", json={"nombre": "X"}).status_code == 401  # sin clave


# ------------------------------------------------------------------ con el segundo factor activo
def test_sin_codigo_es_401_con_codigo_correcto_pasa_y_repetirlo_es_401(ctx):
    _, secreto = activar(ctx)
    poner_hora(ctx, T0 + 60)
    codigo = totp.codigo(secreto, T0 + 60)
    r = crear(ctx)
    assert r.status_code == 401 and r.json()["title"] == "Falta el código de verificación"
    assert exitosa(crear(ctx, codigo))
    r = crear(ctx, codigo)  # el mismo código otra vez
    assert r.status_code == 401 and r.json()["title"] == "Código de verificación no válido"


def test_cada_una_de_las_4_acciones_acepta_su_codigo_y_rechaza_uno_malo(ctx):
    _, secreto = activar(ctx)
    for i, nombre in enumerate(ACCIONES):
        t = T0 + 120 + 30 * i  # un paso nuevo por acción
        poner_hora(ctx, t)
        assert ejecutar(ctx, nombre, "000000").status_code == 401, nombre  # un código malo
        assert exitosa(ejecutar(ctx, nombre, totp.codigo(secreto, t))), (
            nombre
        )  # y uno bueno (que además pone los fallos en cero)


def test_se_toleran_30_segundos_de_desfase_de_reloj_pero_no_60(ctx):
    _, secreto = activar(ctx)
    poner_hora(ctx, T0 + 300)
    assert crear(ctx, totp.codigo(secreto, T0 + 300 - 60)).status_code == 401  # dos pasos atrás
    assert crear(ctx, totp.codigo(secreto, T0 + 300 + 60)).status_code == 401  # dos pasos adelante
    assert exitosa(crear(ctx, totp.codigo(secreto, T0 + 300 - 30)))  # un paso atrás
    assert exitosa(crear(ctx, totp.codigo(secreto, T0 + 300 + 30)))  # un paso adelante (es más nuevo que el anterior)


def test_un_codigo_mas_viejo_que_el_ultimo_aceptado_no_sirve(ctx):
    _, secreto = activar(ctx)
    poner_hora(ctx, T0 + 300)
    assert exitosa(crear(ctx, totp.codigo(secreto, T0 + 300 + 30)))  # se usa el del paso siguiente (reloj adelantado)
    assert (
        crear(ctx, totp.codigo(secreto, T0 + 300)).status_code == 401
    )  # el del paso actual ya es más viejo que el último aceptado


@pytest.mark.parametrize("raro", ["12345", "1234567", "12345a", " 123456", "x" * 10_000, "123456" * 500])
def test_un_codigo_raro_da_401_sin_romper_nada(ctx, raro):
    activar(ctx)
    r = crear(ctx, raro)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")


# ------------------------------------------------------------------ bloqueo
def test_cinco_codigos_malos_seguidos_bloquean_la_clave_15_minutos(ctx):
    clave_id, secreto = activar(ctx)
    poner_hora(ctx, T0 + 600)
    estados = [crear(ctx, f"{i:06d}").status_code for i in range(5)]
    assert estados == [401, 401, 401, 401, 429]  # el quinto fallo ya bloquea
    r = crear(ctx, totp.codigo(secreto, T0 + 600))  # ni un código CORRECTO pasa durante el bloqueo
    assert (
        r.status_code == 429
        and 1 <= int(r.headers["retry-after"]) <= 900
        and r.json()["title"].startswith("Clave bloqueada")
    )
    poner_hora(ctx, T0 + 600 + 899)
    assert crear(ctx, totp.codigo(secreto, T0 + 600 + 899)).status_code == 429  # falta 1 segundo
    poner_hora(ctx, T0 + 600 + 901)
    assert exitosa(crear(ctx, totp.codigo(secreto, T0 + 600 + 901)))  # vencido el bloqueo, vuelve a funcionar
    assert ctx.app.state.almacen.mfa_estado(clave_id) == "activa"


def test_un_acierto_pone_los_fallos_en_cero_y_un_bloqueo_vencido_empieza_de_nuevo(ctx):
    _, secreto = activar(ctx)
    t = T0 + 1000
    poner_hora(ctx, t)
    assert [crear(ctx, f"{i:06d}").status_code for i in range(4)] == [401] * 4
    assert exitosa(crear(ctx, totp.codigo(secreto, t)))  # el acierto borra los 4 fallos
    assert [crear(ctx, f"{i:06d}").status_code for i in range(4)] == [401] * 4  # otros 4: todavía no se bloquea
    assert crear(ctx, f"{9:06d}").status_code == 429  # el 5.º seguido sí
    t2 = t + 1000  # vence el bloqueo
    poner_hora(ctx, t2)
    assert (
        crear(ctx, "111111").status_code == 401 and crear(ctx, "111112").status_code == 401
    )  # empieza de cero: no bloquea al primer fallo
    assert [crear(ctx, f"{i:06d}").status_code for i in range(3)] == [
        401,
        401,
        429,
    ]  # y el 5.º desde el reinicio vuelve a bloquear


def test_el_bloqueo_es_de_una_clave_no_de_todas(ctx):
    alm = ctx.app.state.almacen
    clave_id, _ = activar(ctx)
    otra_id, otro_secreto = alm.crear_usuario("admin", "Otra Admin")[0], None
    otra_clave = alm.crear_usuario("admin", "Tercera Admin")
    # la 2.ª clave de admin con su propio segundo factor sigue funcionando mientras la 1.ª está bloqueada
    secreto2 = alm.mfa_iniciar(otra_clave[0])
    poner_hora(ctx, T0 + 2000)
    assert alm.mfa_confirmar(otra_clave[0], totp.codigo(secreto2, T0 + 2000), T0 + 2000)
    for i in range(5):
        crear(ctx, f"{i:06d}")
    assert crear(ctx, "000001").status_code == 429
    h2 = {"X-API-Key": otra_clave[1], "X-TOTP": totp.codigo(secreto2, T0 + 2000 + 30)}
    poner_hora(ctx, T0 + 2000 + 30)
    assert exitosa(ctx.cliente.post("/v1/comercios", json={"nombre": "Desde la otra"}, headers=h2))
    assert otra_id is not None and otro_secreto is None


# ------------------------------------------------------------------ concurrencia
def test_el_mismo_codigo_a_la_vez_lo_acepta_exactamente_una_peticion_en_60_rondas(ctx):
    _, secreto = activar(ctx)
    alm = ctx.app.state.almacen
    antes = len(alm.listar_comercios())
    gano = []
    for ronda in range(60):
        t = T0 + 4000 + 30 * ronda  # cada ronda es un paso de tiempo nuevo
        poner_hora(ctx, t)
        codigo = totp.codigo(secreto, t)
        with ThreadPoolExecutor(3) as pool:
            estados = list(pool.map(lambda _: crear(ctx, codigo, cliente=TestClient(ctx.app)).status_code, range(3)))
        gano.append(sorted(estados))
    assert all(g == [201, 401, 401] for g in gano), [g for g in gano if g != [201, 401, 401]][:3]
    assert (
        len(alm.listar_comercios()) - antes == 60
    )  # lo aceptado es exactamente una por ronda: 60 comercios, ni uno más


def test_dos_procesos_con_la_misma_base_tampoco_aceptan_el_mismo_codigo_dos_veces(ctx):
    """Dos Almacen distintos sobre el mismo archivo simulan 2 procesos: cada uno tiene su candado propio, así que lo que protege es SQLite."""
    clave_id, secreto = activar(ctx)
    a, b = ctx.app.state.almacen, Almacen(ctx.bd, PEPPER)
    resultados = []
    for ronda in range(60):
        t = T0 + 9000 + 30 * ronda
        codigo = totp.codigo(secreto, t)
        with ThreadPoolExecutor(3) as pool:
            r = list(pool.map(lambda alm: alm.mfa_verificar(clave_id, codigo, t)[0], (a, b, a)))
        resultados.append(sorted(r))
    assert all(r == ["codigo_invalido", "codigo_invalido", "ok"] for r in resultados), [
        r for r in resultados if r != ["codigo_invalido"] * 2 + ["ok"]
    ][:3]


def test_los_fallos_se_cuentan_entre_procesos_y_el_bloqueo_se_ve_desde_el_otro(ctx):
    clave_id, secreto = activar(ctx)
    a, b = ctx.app.state.almacen, Almacen(ctx.bd, PEPPER)
    t = T0 + 20_000
    assert [
        a.mfa_verificar(clave_id, "000001", t)[0],
        b.mfa_verificar(clave_id, "000002", t)[0],
        a.mfa_verificar(clave_id, "000003", t)[0],
        b.mfa_verificar(clave_id, "000004", t)[0],
    ] == ["codigo_invalido"] * 4
    assert a.mfa_verificar(clave_id, "000005", t)[0] == "bloqueada"  # el 5.º, repartido entre dos procesos, bloquea
    assert b.mfa_verificar(clave_id, totp.codigo(secreto, t), t)[0] == "bloqueada"  # y el otro proceso lo ve


# ------------------------------------------------------------------ nada secreto sale a la vista
def test_ni_el_secreto_ni_los_codigos_aparecen_en_registros_metricas_errores_ni_auditoria(ctx):
    flujo = io.StringIO()
    manejador = ob.configurar_logs("json", flujo=flujo)
    try:
        alm = ctx.app.state.almacen
        clave_id = alm.autenticar(ctx.claves["admin"]).clave_id
        secreto = alm.mfa_iniciar(clave_id)
        assert alm.mfa_confirmar(clave_id, totp.codigo(secreto, T0), T0)
        poner_hora(ctx, T0 + 60)
        buenos = [totp.codigo(secreto, T0 + 60)]
        malos = ["424242", "737373"]
        respuestas = [crear(ctx, c) for c in malos + buenos + buenos]  # malos, el bueno y el bueno repetido
        texto_respuestas = " ".join(r.text + str(dict(r.headers)) for r in respuestas)
        metricas = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text
        auditoria = " ".join(
            f"{r[0]}|{r[1]}" for r in sqlite3.connect(ctx.bd).execute("SELECT accion, detalle FROM auditoria")
        )
        todo = {
            "registros": flujo.getvalue(),
            "respuestas": texto_respuestas,
            "métricas": metricas,
            "auditoría": auditoria,
        }
        for secreto_ajeno in [secreto, secreto[:16], *malos, *buenos, totp.uri(secreto, "x")]:
            for donde, texto in todo.items():
                assert not aparece(texto, secreto_ajeno), f"«{secreto_ajeno[:8]}…» aparece en {donde}"
        assert (
            "mfa_activado" in auditoria and "mfa_fallido" in auditoria
        )  # sí queda constancia de lo que pasó, sin los valores
    finally:
        logging.getLogger("modelo_ia").handlers = [
            h for h in logging.getLogger("modelo_ia").handlers if h is not manejador
        ]
        logging.getLogger("antifraude").handlers = [
            h for h in logging.getLogger("antifraude").handlers if h is not manejador
        ]


def test_la_auditoria_registra_cada_paso_del_segundo_factor_y_no_se_puede_editar(ctx):
    alm = ctx.app.state.almacen
    clave_id, secreto = activar(ctx)
    poner_hora(ctx, T0 + 60)
    crear(ctx, "000000")
    for i in range(4):
        crear(ctx, f"{i + 1:06d}")  # el 5.º fallo seguido bloquea
    alm.mfa_desactivar(clave_id)
    acciones = [
        r[0]
        for r in sqlite3.connect(ctx.bd).execute("SELECT accion FROM auditoria WHERE accion LIKE 'mfa_%' ORDER BY id")
    ]
    assert acciones == [
        "mfa_iniciado",
        "mfa_activado",
        "mfa_fallido",
        "mfa_fallido",
        "mfa_fallido",
        "mfa_fallido",
        "mfa_bloqueada",
        "mfa_desactivado",
    ]
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(ctx.bd).execute("UPDATE auditoria SET accion='nada' WHERE accion LIKE 'mfa_%'")


def test_las_metricas_cuentan_cada_resultado_sin_etiquetas_peligrosas(ctx):
    _, secreto = activar(ctx)
    antes = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text

    def v(texto, r):
        m = re.search(rf'^antifraude_mfa_total{{resultado="{r}"}} ([0-9.e+-]+)$', texto, re.M)
        return float(m.group(1)) if m else 0.0

    poner_hora(ctx, T0 + 60)
    crear(ctx)  # falta_codigo
    crear(ctx, "000000")  # codigo_invalido
    crear(ctx, totp.codigo(secreto, T0 + 60))  # ok
    despues = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text
    assert {
        r: v(despues, r) - v(antes, r) for r in ("falta_codigo", "codigo_invalido", "ok", "bloqueada", "no_configurado")
    } == {
        "falta_codigo": 1,
        "codigo_invalido": 1,
        "ok": 1,
        "bloqueada": 0,
        "no_configurado": 0,
    }
    assert set(re.findall(r'antifraude_mfa_total\{resultado="([^"]+)"\}', despues)) <= {
        "ok",
        "falta_codigo",
        "codigo_invalido",
        "bloqueada",
        "no_configurado",
    }


# ------------------------------------------------------------------ la consola
@pytest.fixture
def consola(tmp_path, monkeypatch):
    ruta = tmp_path / "consola.sqlite3"
    monkeypatch.setenv("MODELO_IA_BD", str(ruta))
    monkeypatch.setenv("MODELO_IA_PEPPER", PEPPER)
    alm = Almacen(ruta, PEPPER)
    alm.inicializar()
    return alm, ruta


def correr(capsys, *args):
    admin.main(list(args))
    return capsys.readouterr().out


def test_la_consola_activa_confirma_y_desactiva_el_segundo_factor(consola, capsys):
    alm, ruta = consola
    admin_id, _ = alm.crear_usuario("admin", "Ana Admin")
    salida = correr(capsys, "activar-mfa", "--id", str(admin_id))
    secreto = re.search(r"secreto: ([A-Z2-7]{32})", salida).group(1)
    assert "NO activo" in salida and "otpauth://totp/" in salida and secreto in salida
    assert "pendiente" in correr(capsys, "listar-claves")
    with pytest.raises(SystemExit, match="no es correcto"):
        admin.main(["confirmar-mfa", "--id", str(admin_id), "--codigo", "000000"])
    assert alm.mfa_estado(admin_id) == "pendiente"
    assert "tiene el segundo factor activo" in correr(
        capsys, "confirmar-mfa", "--id", str(admin_id), "--codigo", totp.codigo(secreto, time.time())
    )
    assert " activo" in correr(capsys, "listar-claves") and alm.mfa_estado(admin_id) == "activa"
    with pytest.raises(SystemExit, match="ya tiene el segundo factor activo"):
        admin.main(["activar-mfa", "--id", str(admin_id)])  # no se pisa un factor activo por accidente
    assert "desactivado" in correr(capsys, "desactivar-mfa", "--id", str(admin_id))
    assert alm.mfa_estado(admin_id) == "sin_mfa"
    with pytest.raises(SystemExit, match="no tiene segundo factor"):
        admin.main(["desactivar-mfa", "--id", str(admin_id)])
    secreto_en_auditoria = " ".join(r[0] or "" for r in sqlite3.connect(ruta).execute("SELECT detalle FROM auditoria"))
    assert secreto not in secreto_en_auditoria


def test_la_consola_solo_activa_el_segundo_factor_en_claves_de_administrador_activas(consola):
    alm, _ = consola
    analista, _ = alm.crear_usuario("analista", "Ana Analista")
    comercio = alm.crear_comercio("Tienda C")
    clave_comercio = alm.crear_clave(comercio, "comercio")
    id_comercio = alm.autenticar(clave_comercio).clave_id
    for malo in (analista, id_comercio, 9999):
        with pytest.raises(SystemExit, match="administrador|activa"):
            admin.main(["activar-mfa", "--id", str(malo)])
    apagada, _ = alm.crear_usuario("admin", "Apagada Admin")
    alm.desactivar_clave(apagada)
    with pytest.raises(SystemExit, match="activa"):
        admin.main(["activar-mfa", "--id", str(apagada)])


def test_rotar_la_clave_de_un_admin_deja_la_nueva_sin_segundo_factor(consola, capsys):
    alm, ruta = consola
    admin_id, _ = alm.crear_usuario("admin", "Rota Admin")
    secreto = alm.mfa_iniciar(admin_id)
    assert alm.mfa_confirmar(admin_id, totp.codigo(secreto, time.time()), time.time())
    assert secreto_guardado(ruta, admin_id) == secreto
    nuevo_id, _ = alm.rotar_clave(admin_id)
    assert (
        alm.mfa_estado(nuevo_id) == "sin_mfa"
    )  # el segundo factor era de la clave anterior: hay que activarlo de nuevo
    assert alm.mfa_estado(admin_id) == "sin_mfa"  # y la vieja, apagada, ya no guarda su secreto
    assert secreto_guardado(ruta, admin_id) is None


def secreto_guardado(ruta, clave_id):
    """Lo que hay en la columna del secreto TOTP de esa clave (directo en la base)."""
    return sqlite3.connect(ruta).execute("SELECT mfa_secreto FROM claves WHERE id=?", (clave_id,)).fetchone()[0]


def test_desactivar_una_clave_de_admin_borra_su_secreto_y_no_toca_las_demas(consola):
    alm, ruta = consola
    a, _ = alm.crear_usuario("admin", "Apagar Admin")
    b, _ = alm.crear_usuario("admin", "Otra Admin")
    pendiente, _ = alm.crear_usuario("admin", "Pendiente Admin")
    for clave_id in (a, b):
        secreto = alm.mfa_iniciar(clave_id)
        assert alm.mfa_confirmar(clave_id, totp.codigo(secreto, time.time()), time.time())
    alm.mfa_iniciar(pendiente)  # también un secreto todavía pendiente
    assert all(secreto_guardado(ruta, i) for i in (a, b, pendiente))
    alm.desactivar_clave(a)
    alm.desactivar_clave(pendiente)
    assert (
        secreto_guardado(ruta, a) is None and secreto_guardado(ruta, pendiente) is None
    )  # se borraron los de las claves apagadas
    assert secreto_guardado(ruta, b) is not None and alm.mfa_estado(b) == "activa"  # la otra clave sigue con el suyo
    fila = (
        sqlite3.connect(ruta)
        .execute("SELECT mfa_confirmada, mfa_ultimo_paso, mfa_fallos, mfa_bloqueo_hasta FROM claves WHERE id=?", (a,))
        .fetchone()
    )
    assert tuple(fila) == (0, 0, 0, None)


# ------------------------------------------------------------------ migración 2
def test_la_migracion_2_agrega_las_columnas_conserva_las_claves_y_hace_una_copia(tmp_path):
    ruta = tmp_path / "v1.sqlite3"
    v1 = Almacen(ruta, PEPPER)
    assert v1.inicializar(v1._migraciones()[:1]) == [1]  # una base como la de antes de esta versión
    clave = v1.crear_clave(1, "admin")
    columnas = {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(claves)")}
    assert "mfa_secreto" not in columnas
    nuevo = Almacen(ruta, PEPPER)
    assert nuevo.inicializar() == [2, 3, 4]
    columnas = {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(claves)")}
    assert {"mfa_secreto", "mfa_confirmada", "mfa_ultimo_paso", "mfa_fallos", "mfa_bloqueo_hasta"} <= columnas
    principal = nuevo.autenticar(clave)  # la clave de antes sigue funcionando
    assert principal is not None and principal.rol == "admin" and nuevo.mfa_estado(principal.clave_id) == "sin_mfa"
    copias = nuevo.copias_previas()
    assert len(copias) == 1 and "-v2-" in copias[0].name
    assert "mfa_secreto" not in {
        r[1] for r in sqlite3.connect(copias[0]).execute("PRAGMA table_info(claves)")
    }  # la copia es la de ANTES
    assert nuevo.inicializar() == []


@pytest.mark.solo_sqlite
def test_la_migracion_2_se_puede_correr_dos_veces_sin_fallar_y_una_base_nueva_ya_trae_las_columnas(tmp_path):
    nuevo = Almacen(tmp_path / "n.sqlite3", PEPPER)
    assert nuevo.inicializar() == [1, 2, 3, 4]
    con = nuevo.base.abrir()
    try:
        nuevo.esquema.m2_mfa_admin(con)  # las columnas ya existen: no debe fallar
        valores = con.execute(
            "SELECT mfa_confirmada, mfa_ultimo_paso, mfa_fallos, mfa_bloqueo_hasta, mfa_secreto FROM claves"
        ).fetchall()
    finally:
        con.close()
    assert valores == []  # base nueva: sin claves todavía, pero la consulta con las 5 columnas funciona


def test_una_base_con_claves_viejas_y_la_exigencia_activa_da_403_claro_no_un_error(tmp_path, artefacto):  # noqa: F811
    ruta = tmp_path / "vieja.sqlite3"
    v1 = Almacen(ruta, "pepper-de-prueba")
    v1.inicializar(v1._migraciones()[:1])
    clave = v1.crear_clave(1, "admin")
    from modelo_ia.api.ajustes import Ajustes
    from modelo_ia.api.app import crear_app

    app = crear_app(
        Ajustes(ruta_bd=ruta, pepper="pepper-de-prueba", exigir_mfa=True), artefacto, reloj=lambda: RELOJ
    )  # al arrancar migra sola
    r = TestClient(app).post("/v1/comercios", json={"nombre": "Nueva"}, headers={"X-API-Key": clave})
    assert r.status_code == 403 and r.json()["title"] == "Segundo factor no configurado"
