"""Suspensión de comercios y registro del acuerdo de datos: permisos, que nada se guarde después de suspender, historial que no se pisa y cero fugas del NIT."""
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from modelo_ia.api import admin
from modelo_ia.api.almacen import PLATAFORMA, Almacen, estado_acuerdo
from modelo_ia.tests.test_api import RELOJ, artefacto, construir_contexto, pago  # noqa: F401  (artefacto es un fixture)

HOY = RELOJ.date()
ACUERDO = {"version": "v1.0", "firmado_en": "2026-11-01", "referencia": "acuerdo_tienda_a_v1.pdf"}


@pytest.fixture
def ctx(tmp_path, artefacto):  # noqa: F811
    return construir_contexto(tmp_path / "c", artefacto)


@pytest.fixture
def ctx_exige(tmp_path, artefacto):  # noqa: F811
    return construir_contexto(tmp_path / "e", artefacto, exigir_acuerdo=True)


def _bd(ctx):
    con = sqlite3.connect(ctx.bd); con.row_factory = sqlite3.Row
    return con


def _auditoria(ctx, accion):
    return [dict(r) for r in _bd(ctx).execute("SELECT * FROM auditoria WHERE accion=? ORDER BY id", (accion,))]


def _suspender(ctx, comercio, quien="admin", motivo="Pagos sospechosos reportados"):
    return ctx.post(f"/v1/comercios/{ctx.comercios.get(comercio, comercio)}/suspender", {"motivo": motivo}, quien)


# ------------------------------------------------------------------ permisos
def test_solo_un_administrador_suspende_reactiva_y_registra_acuerdos(ctx):
    A = ctx.comercios["A"]
    for quien in ("A", "B", "analista"):                                                                 # ni un comercio (ni siquiera el suyo), ni un analista
        assert _suspender(ctx, "A", quien).status_code == 403
        assert ctx.post(f"/v1/comercios/{A}/reactivar", None, quien).status_code == 403
        assert ctx.cliente.put(f"/v1/comercios/{A}/acuerdo", json=ACUERDO, headers=ctx.h(quien)).status_code == 403
    assert _bd(ctx).execute("SELECT activo FROM comercios WHERE id=?", (A,)).fetchone()[0] == 1          # y no pasó nada
    assert ctx.cliente.post(f"/v1/comercios/{A}/suspender", json={"motivo": "x" * 5}).status_code == 401  # sin clave
    assert ctx.cliente.post(f"/v1/comercios/{A}/suspender", json={"motivo": "x" * 5}, headers={"X-API-Key": "inventada"}).status_code == 401
    assert _suspender(ctx, "A").status_code == 200


def test_los_listados_son_solo_para_el_personal_y_el_nit_solo_para_el_administrador(ctx):
    ctx.cliente.put(f"/v1/comercios/{ctx.comercios['A']}/acuerdo", json=ACUERDO, headers=ctx.h("admin"))
    nuevo = ctx.post("/v1/comercios", {"nombre": "Tienda Z", "razon_social": "Z Comercial SAS", "nit": "900.123.456-7"}, "admin")
    assert nuevo.status_code == 201
    for quien in ("A", "B"):
        assert ctx.cliente.get("/v1/comercios", headers=ctx.h(quien)).status_code == 403
        assert ctx.cliente.get(f"/v1/comercios/{ctx.comercios['A']}/acuerdos", headers=ctx.h(quien)).status_code == 403
    como_analista = ctx.cliente.get("/v1/comercios", headers=ctx.h("analista")).json()
    como_admin = ctx.cliente.get("/v1/comercios", headers=ctx.h("admin")).json()
    z = lambda lista: next(c for c in lista if c["nombre"] == "Tienda Z")
    assert z(como_analista)["nit"] is None and z(como_admin)["nit"] == "900.123.456-7" and z(como_analista)["razon_social"] == "Z Comercial SAS"
    assert PLATAFORMA not in [c["id"] for c in como_admin] and {c["nombre"] for c in como_admin} == {"Tienda A", "Tienda B", "Tienda Z"}


def test_el_nit_nunca_sale_por_otro_lado_ni_se_guarda_en_la_auditoria(ctx):
    r = ctx.post("/v1/comercios", {"nombre": "Tienda N", "nit": "800.555.444-1"}, "admin")
    assert r.status_code == 201 and "800.555.444" not in r.text
    for ruta in ("/v1/panel/comercios", "/v1/yo"):
        for quien in ("analista", "admin"):
            assert "800.555.444" not in ctx.cliente.get(ruta, headers=ctx.h(quien)).text, ruta
    assert "800.555.444" not in json.dumps(_auditoria(ctx, "comercio_creado")) and _auditoria(ctx, "comercio_creado")[-1]["detalle"].count("con_nit") == 1


# ------------------------------------------------------------------ suspensión
def test_un_comercio_suspendido_no_puede_hacer_nada_y_se_le_dice_claro(ctx):
    r = ctx.evaluar(pago("S1", "CS1")); assert r.status_code == 201 and ctx.post("/v1/lista-negra/CLI-X", None, "A") is not None
    assert _suspender(ctx, "A").json() == {"id_comercio": ctx.comercios["A"], "estado": "suspendido", "cambio": True}
    idev = r.json()["id_evaluacion"]
    llamadas = [ctx.evaluar(pago("S2", "CS2")), ctx.cliente.get(f"/v1/evaluaciones/{idev}", headers=ctx.h("A")), ctx.post(f"/v1/evaluaciones/{idev}/resultado", {"resultado": "FRAUDE_CONFIRMADO"}, "A"),
                ctx.cliente.get("/v1/lista-negra", headers=ctx.h("A")), ctx.cliente.put("/v1/lista-negra/CLI-Y", headers=ctx.h("A")),
                ctx.cliente.delete("/v1/lista-negra/CLI-X", headers=ctx.h("A")), ctx.cliente.get("/v1/yo", headers=ctx.h("A")), ctx.cliente.get("/v1/panel/transacciones", headers=ctx.h("A"))]
    assert [x.status_code for x in llamadas] == [403] * len(llamadas)
    assert all(x.json()["title"] == "Comercio suspendido" and "suspendido" in x.json()["detail"] for x in llamadas)       # 403 claro, NO el 401 de una clave falsa
    assert ctx.evaluar(pago("S3", "CS3"), quien="B").status_code == 201                                                  # otro comercio sigue igual


def test_una_clave_inventada_sigue_dando_401_y_no_revela_nada(ctx):
    _suspender(ctx, "A")
    falsa = ctx.cliente.get("/v1/yo", headers={"X-API-Key": ctx.claves["A"][:-3] + "xyz"})
    assert falsa.status_code == 401 and "suspendido" not in falsa.text


def test_el_equipo_sigue_funcionando_y_las_revisiones_pendientes_se_pueden_resolver_mientras_esta_suspendido(ctx):
    ids = []
    for i in range(40):
        r = ctx.evaluar(pago(f"RV{i}", f"CRV{i}"))
        ids.append(r.json())
    pendientes = ctx.cliente.get("/v1/revisiones", headers=ctx.h("analista")).json()["revisiones"]
    _suspender(ctx, "A")
    assert ctx.cliente.get("/v1/yo", headers=ctx.h("analista")).status_code == 200 and ctx.cliente.get("/v1/yo", headers=ctx.h("admin")).status_code == 200
    assert len(ctx.cliente.get("/v1/revisiones", headers=ctx.h("analista")).json()["revisiones"]) == len(pendientes)    # sus casos siguen en la cola
    if pendientes:
        r = ctx.post(f"/v1/revisiones/{pendientes[0]['id_evaluacion']}/resolver", {"decision": "APROBADA"}, "analista")
        assert r.status_code == 200
    assert ctx.cliente.get("/v1/panel/transacciones", headers=ctx.h("analista")).json()["total"] >= 1                # y se ve su historial


def test_repetir_la_suspension_o_la_reactivacion_es_seguro_y_la_auditoria_no_se_ensucia(ctx):
    assert _suspender(ctx, "A").json()["cambio"] is True and _suspender(ctx, "A").json()["cambio"] is False
    assert len(_auditoria(ctx, "comercio_suspendido")) == 1
    r = ctx.post(f"/v1/comercios/{ctx.comercios['A']}/reactivar", None, "admin")
    assert r.json() == {"id_comercio": ctx.comercios["A"], "estado": "activo", "cambio": True}
    assert ctx.post(f"/v1/comercios/{ctx.comercios['A']}/reactivar", None, "admin").json()["cambio"] is False and len(_auditoria(ctx, "comercio_reactivado")) == 1
    assert ctx.evaluar(pago("OK1", "COK1")).status_code == 201                                                          # la clave vuelve a funcionar
    fila = _bd(ctx).execute("SELECT suspendido_en, motivo_suspension FROM comercios WHERE id=?", (ctx.comercios["A"],)).fetchone()
    assert tuple(fila) == (None, None)


def test_la_auditoria_dice_quien_suspendio_y_por_que(ctx):
    _suspender(ctx, "A", motivo="Reporte de contracargos masivos")
    a = _auditoria(ctx, "comercio_suspendido")[0]
    id_admin = _bd(ctx).execute("SELECT id FROM claves WHERE rol='admin'").fetchone()[0]
    assert a["comercio_id"] == ctx.comercios["A"] and a["clave_id"] == id_admin and "contracargos" in a["detalle"]
    with pytest.raises(sqlite3.DatabaseError, match="no se edita"):
        _bd(ctx).execute("UPDATE auditoria SET accion='nada' WHERE id=?", (a["id"],))


def test_la_plataforma_no_se_puede_suspender_y_el_equipo_nunca_pierde_el_acceso(ctx):
    r = _suspender(ctx, PLATAFORMA)
    assert r.status_code == 409 and "plataforma" in r.json()["detail"]
    with pytest.raises(Exception, match="plataforma"):
        ctx.app.state.almacen.suspender_comercio(PLATAFORMA, "intento directo al almacén")
    assert ctx.cliente.get("/v1/yo", headers=ctx.h("admin")).status_code == 200 and _bd(ctx).execute("SELECT activo FROM comercios WHERE id=1").fetchone()[0] == 1
    assert ctx.post(f"/v1/comercios/{PLATAFORMA}/reactivar", None, "admin").json()["cambio"] is False                  # reactivar la plataforma es inofensivo
    assert ctx.cliente.put(f"/v1/comercios/{PLATAFORMA}/acuerdo", json=ACUERDO, headers=ctx.h("admin")).status_code == 409


def test_entradas_raras_dan_errores_claros_y_nunca_un_500(ctx):
    for malo in (0, -1, 99999):
        assert _suspender(ctx, malo).status_code in (404, 422), malo
    assert ctx.post("/v1/comercios/abc/suspender", {"motivo": "motivo valido"}, "admin").status_code == 422
    assert ctx.post("/v1/comercios/99999/reactivar", None, "admin").status_code == 404
    for motivo in ("", "  ", "ab", "x" * 201, "Ana <script>alert(1)</script>", "con\x00nulo", "salto\nde\x07linea"):
        assert _suspender(ctx, "A", motivo=motivo).status_code == 422, repr(motivo)
    assert ctx.post(f"/v1/comercios/{ctx.comercios['A']}/suspender", {"motivo": "valido", "extra": 1}, "admin").status_code == 422
    assert ctx.post(f"/v1/comercios/{ctx.comercios['A']}/suspender", {}, "admin").status_code == 422
    assert _bd(ctx).execute("SELECT activo FROM comercios WHERE id=?", (ctx.comercios["A"],)).fetchone()[0] == 1


def test_un_motivo_con_sql_se_guarda_como_texto_y_no_rompe_nada(ctx):
    motivo = "x'); DROP TABLE comercios;--"
    assert _suspender(ctx, "A", motivo=motivo).status_code == 200
    assert _bd(ctx).execute("SELECT motivo_suspension FROM comercios WHERE id=?", (ctx.comercios["A"],)).fetchone()[0] == motivo
    assert _bd(ctx).execute("SELECT COUNT(*) FROM comercios").fetchone()[0] == 3


# ------------------------------------------------------------------ lo que no se guarda después de suspender
def test_si_lo_suspenden_mientras_se_calcula_el_pago_no_se_guarda(ctx, monkeypatch):
    """La comprobación se hace DENTRO del bloqueo de guardado: el comercio pasó la autenticación, pero cuando llega a guardar ya está suspendido."""
    serv, alm = ctx.app.state.servicio, ctx.app.state.almacen
    original, hecho = serv._calcular, []

    def calcular_y_suspender(*a, **k):
        if not hecho:
            hecho.append(alm.suspender_comercio(ctx.comercios["A"], "suspendido en pleno cálculo"))
        return original(*a, **k)
    monkeypatch.setattr(serv, "_calcular", calcular_y_suspender)
    r = ctx.evaluar(pago("CARRERA", "CCAR"))
    assert hecho == [True] and r.status_code == 403 and r.json()["title"] == "Comercio suspendido"
    assert _bd(ctx).execute("SELECT COUNT(*) FROM evaluaciones WHERE comercio_id=?", (ctx.comercios["A"],)).fetchone()[0] == 0


def test_concurrencia_30_rondas_nada_se_guarda_despues_de_suspender(ctx):
    alm, app = ctx.app.state.almacen, ctx.app
    total_201 = total_403 = 0
    for ronda in range(30):
        cid = alm.crear_comercio(f"Ronda {ronda}")
        clave = alm.crear_clave(cid, "comercio")
        h = {"X-API-Key": clave}
        resultados, listo = [], threading.Barrier(7)

        def evaluador(n):
            c = TestClient(app); listo.wait()
            for k in range(12):
                r = c.post("/v1/evaluaciones", json=pago(f"R{ronda}-{n}-{k}", f"CR{ronda}-{n}"), headers=h)
                resultados.append(r.status_code)
                if r.status_code == 403:
                    break

        def suspensor():
            c = TestClient(app); listo.wait(); time.sleep(0.03)
            assert c.post(f"/v1/comercios/{cid}/suspender", json={"motivo": "prueba de concurrencia"}, headers=ctx.h("admin")).status_code == 200

        with ThreadPoolExecutor(7) as ex:
            fs = [ex.submit(evaluador, n) for n in range(6)] + [ex.submit(suspensor)]
            [f.result() for f in fs]
        con = _bd(ctx)
        filas = con.execute("SELECT COUNT(*) FROM evaluaciones WHERE comercio_id=?", (cid,)).fetchone()[0]
        susp = con.execute("SELECT suspendido_en FROM comercios WHERE id=?", (cid,)).fetchone()[0]
        ultima = con.execute("SELECT MAX(creada) FROM evaluaciones WHERE comercio_id=?", (cid,)).fetchone()[0]
        assert set(resultados) <= {201, 403, 429}, resultados
        assert filas == resultados.count(201), (ronda, filas, resultados)                        # todo lo aceptado está guardado; lo rechazado no dejó nada
        assert ultima is None or ultima <= susp, (ronda, ultima, susp)                           # NINGUNA evaluación se guardó después de suspender
        assert TestClient(app).post("/v1/evaluaciones", json=pago(f"POST-{ronda}", "CPOST"), headers=h).status_code == 403     # y las nuevas ya no entran
        total_201 += resultados.count(201); total_403 += resultados.count(403)
    assert total_201 > 0 and total_403 > 0                                                       # la carrera de verdad ocurrió (si no, la prueba no probaba nada)


# ------------------------------------------------------------------ acuerdo de datos
def _acuerdo(ctx, comercio="A", **cambios):
    return ctx.cliente.put(f"/v1/comercios/{ctx.comercios.get(comercio, comercio)}/acuerdo", json={**ACUERDO, **cambios}, headers=ctx.h("admin"))


def test_el_estado_del_acuerdo_cambia_con_el_tiempo():
    class F(dict):
        pass
    fila = lambda hasta: {"version": "v1", "firmado_en": "2026-01-01", "vigente_hasta": hasta}
    assert estado_acuerdo(None, HOY)["estado"] == "sin_acuerdo"
    assert estado_acuerdo(fila(None), HOY)["estado"] == "vigente" and estado_acuerdo(fila(None), HOY)["dias_para_vencer"] is None
    assert estado_acuerdo(fila((HOY + timedelta(days=31)).isoformat()), HOY)["estado"] == "vigente"
    assert estado_acuerdo(fila((HOY + timedelta(days=30)).isoformat()), HOY)["estado"] == "vence_pronto"
    assert estado_acuerdo(fila(HOY.isoformat()), HOY)["estado"] == "vence_pronto" and estado_acuerdo(fila(HOY.isoformat()), HOY)["dias_para_vencer"] == 0
    assert estado_acuerdo(fila((HOY - timedelta(days=1)).isoformat()), HOY)["estado"] == "vencido"


def test_registrar_un_acuerdo_lo_deja_vigente_y_el_ultimo_registrado_es_el_que_vale(ctx):
    A = ctx.comercios["A"]
    antes = next(c for c in ctx.cliente.get("/v1/comercios", headers=ctx.h("analista")).json() if c["id"] == A)
    assert antes["acuerdo"]["estado"] == "sin_acuerdo"
    r = _acuerdo(ctx); assert r.status_code == 201 and r.json()["acuerdo"]["estado"] == "vigente" and r.json()["acuerdo"]["version"] == "v1.0"
    assert _acuerdo(ctx, version="v2.0", firmado_en="2026-11-10", vigente_hasta="2027-11-10").status_code == 201
    h = ctx.cliente.get(f"/v1/comercios/{A}/acuerdos", headers=ctx.h("analista")).json()
    assert [x["version"] for x in h] == ["v2.0", "v1.0"] and [x["estado"] for x in h] == ["vigente", "reemplazado"] and h[0]["referencia"] == ACUERDO["referencia"]
    fila = next(c for c in ctx.cliente.get("/v1/comercios", headers=ctx.h("analista")).json() if c["id"] == A)
    assert fila["acuerdo"]["version"] == "v2.0" and fila["acuerdo"]["vigente_hasta"] == "2027-11-10"
    assert ctx.cliente.get("/v1/comercios/99999/acuerdos", headers=ctx.h("analista")).status_code == 404


def test_el_historial_de_acuerdos_no_se_edita_ni_se_borra(ctx):
    _acuerdo(ctx)
    con = _bd(ctx)
    with pytest.raises(sqlite3.DatabaseError, match="no se editan"):
        con.execute("UPDATE acuerdos SET version='trampa'")
    with pytest.raises(sqlite3.DatabaseError, match="no se borran"):
        con.execute("DELETE FROM acuerdos")
    assert con.execute("SELECT version FROM acuerdos").fetchone()[0] == "v1.0"


def test_un_acuerdo_con_datos_malos_se_rechaza_con_un_mensaje(ctx):
    malos = [dict(version=""), dict(version="v" * 41), dict(version="v1<script>"), dict(referencia=""), dict(referencia="x" * 121), dict(referencia="../../etc/passwd"),
             dict(referencia="a\\b"), dict(firmado_en="2026-13-45"), dict(firmado_en="no es fecha"), dict(firmado_en="2027-01-01"),
             dict(vigente_hasta="2026-10-31"), dict(vigente_hasta="ayer")]
    for cambio in malos:
        r = _acuerdo(ctx, **cambio)
        assert r.status_code == 422, (cambio, r.status_code)
    assert _acuerdo(ctx, extra="no").status_code == 422
    assert _bd(ctx).execute("SELECT COUNT(*) FROM acuerdos").fetchone()[0] == 0
    assert _acuerdo(ctx, referencia="sha256:" + "a1" * 32).status_code == 201                  # una huella SHA-256 sí es una referencia válida
    assert _acuerdo(ctx, comercio=99999).status_code == 404


def test_el_acuerdo_guarda_datos_no_el_documento_y_queda_en_la_auditoria(ctx):
    _acuerdo(ctx)
    a = _auditoria(ctx, "acuerdo_registrado")
    assert len(a) == 1 and "v1.0" in a[0]["detalle"] and "acuerdo_tienda_a_v1.pdf" in a[0]["detalle"] and a[0]["comercio_id"] == ctx.comercios["A"]
    columnas = {r[1] for r in _bd(ctx).execute("PRAGMA table_info(acuerdos)")}
    assert not any(c in columnas for c in ("contenido", "documento", "archivo", "pdf", "blob"))


# ------------------------------------------------------------------ exigir el acuerdo (ajuste apagado por defecto)
def test_por_defecto_no_se_exige_acuerdo_para_no_romper_la_demo(ctx):
    assert ctx.evaluar(pago("D1", "CD1")).status_code == 201


def test_con_el_ajuste_activo_solo_evalua_quien_tiene_un_acuerdo_vigente(ctx_exige):
    c = ctx_exige
    r = c.evaluar(pago("X1", "CX1"))
    assert r.status_code == 403 and r.json()["title"] == "Sin acuerdo de datos vigente" and _bd(c).execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] == 0
    assert _acuerdo(c, vigente_hasta="2026-11-11").status_code == 201                          # vencido ayer
    assert c.evaluar(pago("X2", "CX2")).status_code == 403
    assert _acuerdo(c, version="v2", vigente_hasta="2027-01-01").status_code == 201
    assert c.evaluar(pago("X3", "CX3")).status_code == 201
    assert c.evaluar(pago("X4", "CX4"), quien="B").status_code == 403                          # el otro comercio no tiene acuerdo
    assert c.cliente.get("/v1/yo", headers=c.h("analista")).status_code == 200                # el equipo no depende de acuerdos


def test_sin_acuerdo_el_comercio_igual_puede_reportar_resultados_y_usar_su_lista_negra(ctx_exige):
    c = ctx_exige
    _acuerdo(c)
    idev = c.evaluar(pago("Y1", "CY1")).json()["id_evaluacion"]
    _acuerdo(c, version="v9", firmado_en="2026-01-01", vigente_hasta="2026-02-01")             # el último registrado vence en el pasado
    assert c.evaluar(pago("Y2", "CY2")).status_code == 403
    r2 = c.post(f"/v1/evaluaciones/{idev}/resultado", {"resultado": "LEGITIMA"}, "A")
    assert r2.status_code in (200, 201), r2.text
    assert c.cliente.put("/v1/lista-negra/CLI-Z", headers=c.h("A")).status_code == 200 and c.cliente.get("/v1/lista-negra", headers=c.h("A")).status_code == 200


def test_el_ajuste_se_lee_del_entorno_y_su_ausencia_genera_un_aviso(monkeypatch):
    from modelo_ia.api.ajustes import Ajustes
    for valor, esperado in (("1", True), ("true", True), ("SI", True), ("0", False), ("", False), ("no", False)):
        monkeypatch.setenv("MODELO_IA_EXIGIR_ACUERDO", valor)
        assert Ajustes.desde_entorno().exigir_acuerdo is esperado, valor
    assert any("EXIGIR_ACUERDO" in a for a in Ajustes(pepper="s" * 40).problemas_de_produccion()["avisos"])
    assert not any("EXIGIR_ACUERDO" in a for a in Ajustes(pepper="s" * 40, exigir_acuerdo=True).problemas_de_produccion()["avisos"])


# ------------------------------------------------------------------ crear comercio (compatibilidad y validación)
def test_crear_un_comercio_solo_con_nombre_sigue_funcionando_como_antes(ctx):
    r = ctx.post("/v1/comercios", {"nombre": "Tienda Vieja"}, "admin")
    assert r.status_code == 201 and set(r.json()) == {"id_comercio", "nombre", "clave_api"}
    assert ctx.cliente.get("/v1/yo", headers={"X-API-Key": r.json()["clave_api"]}).json()["rol"] == "comercio"
    for malo in ({"nombre": "T", "nit": "x"}, {"nombre": "Tienda", "nit": "123"}, {"nombre": "Tienda", "nit": "12345678'; DROP"}, {"nombre": "Tienda", "razon_social": "<b>x</b>"},
                 {"nombre": "Tienda", "razon_social": "a"}, {"nombre": "Tienda", "razon_social": "x" * 121}):
        assert ctx.post("/v1/comercios", malo, "admin").status_code == 422, malo
    assert ctx.post("/v1/comercios", {"nombre": "Tienda", "nit": "900123456-7"}, "analista").status_code == 403


def test_la_clave_de_un_comercio_nuevo_queda_registrada_con_quien_la_creo(ctx):
    r = ctx.post("/v1/comercios", {"nombre": "Tienda Q"}, "admin")
    fila = _bd(ctx).execute("SELECT creada_por FROM claves WHERE etiqueta='alta por API'").fetchone()
    assert r.status_code == 201 and fila[0] is not None


# ------------------------------------------------------------------ bases viejas
def test_una_base_de_antes_de_esta_version_se_actualiza_sin_perder_nada(tmp_path):
    ruta = tmp_path / "vieja.sqlite3"
    a = Almacen(ruta, "p" * 40); a.inicializar()
    cid = a.crear_comercio("Tienda Vieja"); clave = a.crear_clave(cid, "comercio")
    con = sqlite3.connect(ruta)
    for col in ("razon_social", "nit", "suspendido_en", "motivo_suspension"):
        con.execute(f"ALTER TABLE comercios DROP COLUMN {col}")
    con.execute("DROP TABLE acuerdos"); con.commit(); con.close()
    b = Almacen(ruta, "p" * 40); b.inicializar(); b.inicializar()                                 # y es repetible
    assert b.autenticar(clave).comercio_id == cid
    cols = {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(comercios)")}
    assert {"razon_social", "nit", "suspendido_en", "motivo_suspension"} <= cols
    assert b.suspender_comercio(cid, "prueba de migración") and b.autenticar(clave) is None and b.comercio_suspendido_por_clave(clave)
    assert b.reactivar_comercio(cid) and b.autenticar(clave).comercio_id == cid
    assert b.registrar_acuerdo(cid, "v1", date(2026, 11, 1), None, "a.pdf", hoy=HOY) == 1


# ------------------------------------------------------------------ pantallas
def test_los_filtros_de_comercio_de_las_pantallas_marcan_los_suspendidos(ctx):
    from modelo_ia.api import documentacion
    _suspender(ctx, "B")
    lista = ctx.cliente.get("/v1/panel/comercios", headers=ctx.h("analista")).json()
    assert {c["nombre"]: c["suspendido"] for c in lista} == {"Tienda A": 0, "Tienda B": 1}
    for js in ("panel/panel.js", "dashboard/dashboard.js"):
        assert '" (suspendido)"' in (documentacion.ESTATICOS / js).read_text(encoding="utf-8"), js


def test_el_contrato_openapi_tiene_los_endpoints_nuevos_y_esta_al_dia():
    from modelo_ia.api import documentacion
    doc = json.loads((documentacion.ESTATICOS.parents[1] / "api" / "openapi.json").read_text(encoding="utf-8"))
    ops = {o["operationId"] for p in doc["paths"].values() for o in p.values()}
    assert {"suspender_comercio", "reactivar_comercio", "registrar_acuerdo", "historial_acuerdos", "listar_comercios"} <= ops


# ------------------------------------------------------------------ línea de comandos
@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MODELO_IA_BD", str(tmp_path / "c.sqlite3")); monkeypatch.setenv("MODELO_IA_PEPPER", "q" * 40)

    def correr(*argv):
        try:
            admin.main(list(argv)); codigo, mensaje = 0, ""
        except SystemExit as e:
            codigo, mensaje = (e.code if isinstance(e.code, int) else 2 if e.code else 0), (e.code if isinstance(e.code, str) else "")
        s = capsys.readouterr()
        return codigo, s.out, s.err + mensaje
    return correr


def test_la_linea_de_comandos_suspende_reactiva_y_registra_acuerdos(cli):
    assert cli("crear-comercio", "--nombre", "Tienda CLI", "--razon-social", "CLI SAS", "--nit", "900.111.222-3")[0] == 0
    c, salida, _ = cli("listar-comercios"); assert "Tienda CLI" in salida and "activo" in salida and "sin_acuerdo" in salida and "900.111" not in salida
    assert "ya no funcionan" in cli("suspender-comercio", "--id", "2", "--motivo", "prueba por consola")[1] and "suspendido" in cli("listar-comercios")[1]
    assert "ya estaba suspendido" in cli("suspender-comercio", "--id", "2", "--motivo", "otra vez")[1]
    assert "vuelven a funcionar" in cli("reactivar-comercio", "--id", "2")[1] and "ya estaba activo" in cli("reactivar-comercio", "--id", "2")[1]
    assert "Acuerdo registrado" in cli("registrar-acuerdo", "--comercio-id", "2", "--version", "v1", "--firmado", "2026-11-01", "--referencia", "a.pdf")[1]
    assert "vigente" in cli("listar-comercios")[1]


def test_la_linea_de_comandos_da_errores_claros(cli):
    cli("crear-comercio", "--nombre", "Tienda CLI")
    for argv in (("suspender-comercio", "--id", "1", "--motivo", "intento sobre la plataforma"), ("suspender-comercio", "--id", "99", "--motivo", "no existe"),
                 ("suspender-comercio", "--id", "2", "--motivo", "x"), ("reactivar-comercio", "--id", "99"),
                 ("registrar-acuerdo", "--comercio-id", "99", "--version", "v1", "--firmado", "2026-11-01", "--referencia", "a.pdf"),
                 ("registrar-acuerdo", "--comercio-id", "2", "--version", "v1", "--firmado", "2099-01-01", "--referencia", "a.pdf"),
                 ("registrar-acuerdo", "--comercio-id", "2", "--version", "v1", "--firmado", "no-fecha", "--referencia", "a.pdf"),
                 ("crear-comercio", "--nombre", "Otra", "--nit", "abc")):
        codigo, _, err = cli(*argv)
        assert codigo != 0 and "Traceback" not in err, argv


# ------------------------------------------------------------------ la misma carrera en las otras escrituras de un comercio
def _suspender_justo_antes_de_guardar(ctx, monkeypatch, funcion):
    """Suspende al comercio A en el instante en que `funcion` (del servicio) abre su bloqueo de guardado: la clave ya pasó la autenticación."""
    import sys
    alm = ctx.app.state.almacen
    original, hecho = alm.transaccion, []

    def transaccion():
        if not hecho and sys._getframe(1).f_code.co_name == funcion:
            hecho.append(alm.suspender_comercio(ctx.comercios["A"], f"suspendido justo antes de {funcion}"))
        return original()
    monkeypatch.setattr(alm, "transaccion", transaccion)
    return hecho


def test_si_lo_suspenden_justo_antes_de_registrar_un_resultado_no_se_guarda(ctx, monkeypatch):
    idev = ctx.evaluar(pago("RES1", "CRES1")).json()["id_evaluacion"]
    hecho = _suspender_justo_antes_de_guardar(ctx, monkeypatch, "registrar_resultado")
    r = ctx.post(f"/v1/evaluaciones/{idev}/resultado", {"resultado": "FRAUDE_CONFIRMADO"}, "A")
    assert hecho == [True] and r.status_code == 403 and r.json()["title"] == "Comercio suspendido"
    assert _bd(ctx).execute("SELECT COUNT(*) FROM resultados").fetchone()[0] == 0


def test_si_lo_suspenden_justo_antes_de_tocar_su_lista_negra_no_cambia(ctx, monkeypatch):
    hecho = _suspender_justo_antes_de_guardar(ctx, monkeypatch, "agregar_lista_negra")
    assert ctx.cliente.put("/v1/lista-negra/CLI-NUEVO", headers=ctx.h("A")).status_code == 403 and hecho == [True]
    assert _bd(ctx).execute("SELECT COUNT(*) FROM lista_negra").fetchone()[0] == 0
    ctx.post(f"/v1/comercios/{ctx.comercios['A']}/reactivar", None, "admin")
    assert ctx.cliente.put("/v1/lista-negra/CLI-QUEDA", headers=ctx.h("A")).status_code == 200
    hecho2 = _suspender_justo_antes_de_guardar(ctx, monkeypatch, "quitar_lista_negra")
    assert ctx.cliente.delete("/v1/lista-negra/CLI-QUEDA", headers=ctx.h("A")).status_code == 403 and hecho2 == [True]
    assert _bd(ctx).execute("SELECT COUNT(*) FROM lista_negra").fetchone()[0] == 1                  # la entrada sigue ahí
