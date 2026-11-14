"""El límite de intentos con claves inventadas, por IP, y cómo se averigua la IP real detrás de un proxy.

Reglas que se vigilan: las claves VÁLIDAS nunca se frenan; solo se cuentan las claves que no existen; X-Forwarded-For solo se cree si la conexión viene de
un proxy declarado confiable (si no, cualquiera elegiría su propia IP); y la IP no se guarda ni sale en ningún registro ni métrica.
"""

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from modelo_ia.api import observabilidad as ob
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.app import crear_app
from modelo_ia.api.ip_cliente import DESCONOCIDA, ip_del_cliente, redes_confiables
from modelo_ia.api.seguridad import LimitadorClavesInvalidas
from modelo_ia.tests.test_api import RELOJ, artefacto, construir_contexto  # noqa: F401  (artefacto es un fixture)

PROXY = redes_confiables("172.16.0.0/12, 10.0.0.0/8")
FALSA = "mia_comercio_" + "NOEXISTE0123456789abcdefghijklmnop"


# ------------------------------------------------------------------ el limitador
def test_los_primeros_20_intentos_pasan_el_21_se_frena_y_dice_cuanto_esperar():
    lim = LimitadorClavesInvalidas(20)
    assert all(lim.registrar("203.0.113.9", ahora=100.0 + i * 0.1) == (True, 0) for i in range(20))
    permitido, espera = lim.registrar("203.0.113.9", ahora=102.5)
    assert permitido is False and 1 <= espera <= 60
    assert lim.registrar("203.0.113.9", ahora=102.6)[0] is False  # y los que siguen también, mientras no pase el minuto


def test_un_intento_rechazado_no_alarga_el_castigo_y_la_ventana_se_desliza():
    lim = LimitadorClavesInvalidas(3)
    for t in (0.0, 1.0, 2.0):
        assert lim.registrar("ip", ahora=t)[0]
    for t in (10.0, 20.0, 30.0, 59.0):
        assert lim.registrar("ip", ahora=t)[0] is False  # rechazados: no quedan contados
    assert lim.registrar("ip", ahora=60.0)[0] is True  # el intento de t=0 ya salió de la ventana de 60 s
    assert lim.registrar("ip", ahora=60.5)[0] is False  # (en la ventana quedan t=1, 2 y 60)
    assert lim.registrar("ip", ahora=61.0)[0] is True  # sale t=1


def test_cada_ip_tiene_su_propio_contador_y_la_desconocida_no_se_limita():
    lim = LimitadorClavesInvalidas(2)
    assert [lim.registrar("a", ahora=1.0)[0], lim.registrar("a", ahora=1.0)[0], lim.registrar("a", ahora=1.0)[0]] == [
        True,
        True,
        False,
    ]
    assert lim.registrar("b", ahora=1.0)[0] is True  # otra IP no se ve afectada
    assert all(lim.registrar(DESCONOCIDA, ahora=1.0) == (True, 0) for _ in range(1000)) and lim.ips_en_memoria() == 2


def test_la_tabla_tiene_tope_y_no_crece_aunque_inventen_millones_de_ips():
    lim = LimitadorClavesInvalidas(5, max_ips=1000)
    for i in range(20_000):
        lim.registrar(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}", ahora=float(i))
    assert lim.ips_en_memoria() == 1000


def test_con_8_hilos_a_la_vez_pasan_exactamente_20_intentos():
    lim = LimitadorClavesInvalidas(20)
    with ThreadPoolExecutor(8) as pool:
        resultados = list(pool.map(lambda _: lim.registrar("203.0.113.9", ahora=50.0)[0], range(400)))
    assert sum(resultados) == 20


# ------------------------------------------------------------------ la IP real
def test_un_cliente_directo_cuenta_con_su_propia_ip_y_una_x_forwarded_for_suya_se_ignora():
    assert ip_del_cliente("203.0.113.9", None, PROXY) == "203.0.113.9"
    assert (
        ip_del_cliente("203.0.113.9", "198.51.100.1", PROXY) == "203.0.113.9"
    )  # no viene de un proxy confiable: se ignora
    assert ip_del_cliente("203.0.113.9", "198.51.100.1", ()) == "203.0.113.9"  # y sin proxies declarados, también


def test_detras_de_un_proxy_confiable_se_toma_la_primera_direccion_no_confiable_de_derecha_a_izquierda():
    assert ip_del_cliente("172.18.0.3", "203.0.113.9", PROXY) == "203.0.113.9"
    assert (
        ip_del_cliente("172.18.0.3", "203.0.113.9, 10.1.2.3", PROXY) == "203.0.113.9"
    )  # un segundo proxy interno en la cadena
    assert (
        ip_del_cliente("172.18.0.3", "1.1.1.1, 203.0.113.9", PROXY) == "203.0.113.9"
    )  # lo que el cliente escribió a la izquierda no cuenta
    assert ip_del_cliente("172.18.0.3", "1.1.1.1, 2.2.2.2, 203.0.113.9", PROXY) == "203.0.113.9"
    assert ip_del_cliente("172.18.0.3", " 203.0.113.9 ", PROXY) == "203.0.113.9"


def test_una_cabecera_con_basura_o_sin_ninguna_direccion_util_no_se_cree():
    assert (
        ip_del_cliente("172.18.0.3", "203.0.113.9, basura", PROXY) == "172.18.0.3"
    )  # la entrada más cercana al proxy es basura: se usa la del proxy
    assert ip_del_cliente("172.18.0.3", "", PROXY) == "172.18.0.3"
    assert (
        ip_del_cliente("172.18.0.3", "10.1.1.1, 10.2.2.2", PROXY) == "172.18.0.3"
    )  # todas internas: no hay cliente que creer
    assert ip_del_cliente("172.18.0.3", "999.1.1.1", PROXY) == "172.18.0.3"


def test_sin_ip_de_origen_es_desconocida_y_no_se_limita():
    for par in (None, "", "testclient", "no-es-ip"):
        assert ip_del_cliente(par, "203.0.113.9", PROXY) == DESCONOCIDA


def test_las_ipv6_se_agrupan_por_su_red_64_y_las_ipv4_mapeadas_se_cuentan_como_ipv4():
    a = ip_del_cliente("2001:db8:1:2:aaaa:bbbb:cccc:dddd", None, ())
    b = ip_del_cliente("2001:db8:1:2:1:2:3:4", None, ())
    c = ip_del_cliente("2001:db8:1:3::1", None, ())
    assert a == b == "2001:db8:1:2::/64" and c == "2001:db8:1:3::/64"
    assert ip_del_cliente("::ffff:203.0.113.9", None, ()) == "203.0.113.9"
    assert ip_del_cliente("172.18.0.3", "2001:db8:1:2:9::9", PROXY) == "2001:db8:1:2::/64"


def test_las_redes_confiables_se_leen_con_espacios_una_ip_sola_y_los_errores_no_pasan_en_silencio():
    assert len(redes_confiables("172.16.0.0/12 , 10.0.0.0/8,203.0.113.5, 2001:db8::/32")) == 4
    assert redes_confiables("") == () and redes_confiables("  ") == ()
    for mal in ("no-es-ip", "10.0.0.0/99", "10.0.0.0/8, x"):
        with pytest.raises(ValueError, match="PROXY_CONFIABLE"):
            redes_confiables(mal)
    assert (
        ip_del_cliente("203.0.113.5", "198.51.100.1", redes_confiables("203.0.113.5")) == "198.51.100.1"
    )  # una IP suelta también es un proxy


# ------------------------------------------------------------------ de punta a punta
@pytest.fixture
def ctx(tmp_path, artefacto):  # noqa: F811
    return construir_contexto(tmp_path / "l", artefacto)


def desde(ctx, ip):
    return TestClient(ctx.app, client=(ip, 50000))


def estados(cliente, n, clave=FALSA, **cab):
    return [cliente.get("/v1/yo", headers={"X-API-Key": clave, **cab}).status_code for _ in range(n)]


def test_20_claves_inventadas_dan_401_y_la_21_da_429_con_retry_after(ctx):
    c = desde(ctx, "203.0.113.9")
    assert estados(c, 20) == [401] * 20
    r = c.get("/v1/yo", headers={"X-API-Key": FALSA})
    assert (
        r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 61 and r.json()["title"] == "Demasiados intentos"
    )
    assert r.json()["request_id"] == r.headers["x-request-id"]  # y el error trae su identificador
    assert estados(c, 5) == [429] * 5


def test_una_clave_valida_nunca_se_frena_ni_siquiera_desde_una_ip_ya_limitada(ctx):
    c = desde(ctx, "203.0.113.9")
    assert estados(c, 25).count(429) == 5
    for quien in ("A", "B", "analista", "admin"):
        assert c.get("/v1/yo", headers=ctx.h(quien)).status_code == 200, quien
    assert estados(desde(ctx, "198.51.100.4"), 3) == [401] * 3  # y otra IP no se ve afectada


def test_solo_se_cuentan_las_claves_que_no_existen(ctx):
    c = desde(ctx, "203.0.113.9")
    for _ in range(100):
        assert c.get("/v1/yo", headers=ctx.h("A")).status_code == 200  # válidas: no cuentan
    assert [c.get("/v1/yo").status_code for _ in range(60)] == [
        401
    ] * 60  # sin cabecera: no es adivinar una clave, no cuenta
    clave_suspendida = ctx.app.state.almacen.crear_clave(ctx.comercios["B"], "comercio")
    ctx.app.state.almacen.suspender_comercio(ctx.comercios["B"], "motivo de prueba")
    assert (
        estados(c, 30, clave=clave_suspendida) == [403] * 30
    )  # un comercio suspendido recibe su 403 claro y tampoco cuenta
    assert estados(c, 20) == [401] * 20 and estados(c, 1) == [429]  # recién ahora se llega al límite con las inventadas


def test_el_cliente_de_pruebas_sin_ip_conocida_no_se_limita(ctx):
    assert (
        estados(ctx.cliente, 60) == [401] * 60
    )  # el cliente de pruebas se presenta como «testclient»: no hay IP que contar


def test_la_x_forwarded_for_de_un_cliente_directo_no_le_sirve_para_cambiar_de_ip(ctx):
    c = desde(ctx, "203.0.113.9")  # sin proxies declarados (valor por defecto)
    codigos = [
        c.get("/v1/yo", headers={"X-API-Key": FALSA, "X-Forwarded-For": f"198.51.100.{i}"}).status_code
        for i in range(25)
    ]
    assert codigos == [401] * 20 + [429] * 5


def test_detras_de_un_proxy_declarado_cada_cliente_real_tiene_su_contador(tmp_path, artefacto):  # noqa: F811
    ctx = construir_contexto(tmp_path / "p", artefacto, proxies_confiables="172.16.0.0/12")
    proxy = desde(ctx, "172.18.0.2")
    assert [
        proxy.get("/v1/yo", headers={"X-API-Key": FALSA, "X-Forwarded-For": "203.0.113.50"}).status_code
        for _ in range(21)
    ] == [401] * 20 + [429]
    assert (
        proxy.get("/v1/yo", headers={"X-API-Key": FALSA, "X-Forwarded-For": "203.0.113.51"}).status_code == 401
    )  # otro cliente tras el mismo proxy
    # un atacante que escribe una X-Forwarded-For falsa a la IZQUIERDA no cambia de contador: Caddy agrega la real a la derecha
    falsos = [
        proxy.get("/v1/yo", headers={"X-API-Key": FALSA, "X-Forwarded-For": f"9.9.9.{i}, 203.0.113.50"}).status_code
        for i in range(3)
    ]
    assert falsos == [429] * 3


def test_sin_declarar_el_proxy_todos_los_clientes_parecen_el_mismo_y_se_comparte_el_contador(ctx):
    proxy = desde(
        ctx, "172.18.0.2"
    )  # es lo que pasaría sin configurar MODELO_IA_PROXY_CONFIABLE: por eso la producción avisa
    codigos = [
        proxy.get("/v1/yo", headers={"X-API-Key": FALSA, "X-Forwarded-For": f"203.0.113.{i}"}).status_code
        for i in range(22)
    ]
    assert codigos == [401] * 20 + [429] * 2


def test_las_metricas_cuentan_rechazadas_y_limitadas_y_nunca_muestran_ips(ctx):
    flujo = io.StringIO()
    manejador = ob.configurar_logs("json", flujo=flujo)
    try:

        def v(texto, r):
            m = re.search(rf'^antifraude_claves_invalidas_total{{resultado="{r}"}} ([0-9.e+-]+)$', texto, re.M)
            return float(m.group(1)) if m else 0.0

        antes = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text
        estados(desde(ctx, "203.0.113.77"), 23)
        despues = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin")).text
        assert (v(despues, "rechazada") - v(antes, "rechazada"), v(despues, "limitada") - v(antes, "limitada")) == (
            20,
            3,
        )
        for texto in (despues, flujo.getvalue()):
            assert "203.0.113.77" not in texto  # la IP solo vive en la memoria del limitador
    finally:
        for nombre in ("modelo_ia", "antifraude"):
            logging.getLogger(nombre).handlers = [h for h in logging.getLogger(nombre).handlers if h is not manejador]


def test_un_valor_mal_escrito_en_la_configuracion_impide_arrancar(tmp_path, artefacto):  # noqa: F811
    base = dict(ruta_bd=tmp_path / "c.sqlite3", pepper="pepper-de-prueba")
    with pytest.raises(ValueError, match="PROXY_CONFIABLE"):
        crear_app(Ajustes(**base, proxies_confiables="esto-no-es-una-red"), artefacto, reloj=lambda: RELOJ)
    for malo in (0, -5):
        with pytest.raises(RuntimeError, match="LIMITE_INVALIDAS"):
            crear_app(Ajustes(**base, limite_claves_invalidas=malo), artefacto, reloj=lambda: RELOJ)


def test_los_ajustes_se_leen_de_las_variables_de_entorno(monkeypatch):
    for nombre in ("MODELO_IA_EXIGIR_MFA", "MODELO_IA_LIMITE_INVALIDAS", "MODELO_IA_PROXY_CONFIABLE"):
        monkeypatch.delenv(nombre, raising=False)
    por_defecto = Ajustes.desde_entorno()
    assert (por_defecto.exigir_mfa, por_defecto.limite_claves_invalidas, por_defecto.proxies_confiables) == (
        False,
        20,
        "",
    )
    monkeypatch.setenv("MODELO_IA_EXIGIR_MFA", "1")
    monkeypatch.setenv("MODELO_IA_LIMITE_INVALIDAS", "7")
    monkeypatch.setenv("MODELO_IA_PROXY_CONFIABLE", " 172.16.0.0/12 ")
    a = Ajustes.desde_entorno()
    assert (a.exigir_mfa, a.limite_claves_invalidas, a.proxies_confiables) == (True, 7, "172.16.0.0/12") and len(
        a.redes_proxy()
    ) == 1
    monkeypatch.setenv("MODELO_IA_EXIGIR_MFA", "no")
    assert Ajustes.desde_entorno().exigir_mfa is False
