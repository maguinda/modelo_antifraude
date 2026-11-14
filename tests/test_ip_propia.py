"""Proveedor de IP «propio» y su descargador: clasificación, honestidad sobre lo que no sabe, descargas atómicas y que la IP nunca se guarde."""

import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from modelo_ia.api import ip_datos, ip_propia
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.app import crear_app
from modelo_ia.api.ip_propia import ProveedorPropio
from modelo_ia.api.proveedores_ip import crear_proveedor
from modelo_ia.tests.test_api import RELOJ, artefacto, con_ip, pago  # noqa: F401  (artefacto es un fixture)


class FalsaBase:
    """Hace de base MMDB: devuelve el registro que se le dé por IP."""

    def __init__(self, registros):
        self.registros = registros

    def get(self, ip):
        return self.registros.get(ip)


def proveedor(ciudad=None, asn=None, nube=(), tor=()):
    """Un ProveedorPropio armado a mano (sin archivos), para probar la lógica de clasificación."""
    p = ProveedorPropio.__new__(ProveedorPropio)
    p._ciudad, p._asn, p._tor, p._nube, p.antiguedad_dias = (
        FalsaBase(ciudad or {}),
        FalsaBase(asn or {}),
        set(tor),
        {},
        0,
    )
    import ipaddress

    for r in nube:
        v, largo, base = ip_propia._red_a_clave(ipaddress.ip_network(r))
        p._nube.setdefault((v, largo), set()).add(base)
    return p


def reg_pais(iso, ciudad="X", lat=1.0, lon=2.0, es=None):
    return {
        "country": {"iso_code": iso, "names": {"es": es or iso}},
        "city": {"names": {"es": ciudad}},
        "location": {"latitude": lat, "longitude": lon},
    }


def org(nombre):
    return {"autonomous_system_number": 1, "autonomous_system_organization": nombre}


# ------------------------------------------------------------------ clasificación
def test_un_operador_normal_en_colombia_es_residencial_con_pais_pero_sin_coordenadas():
    p = proveedor({"190.1.1.1": reg_pais("CO", "Medellín", 6.2, -75.5)}, {"190.1.1.1": org("EMPRESAS PUBLICAS")})
    i = p.consultar("190.1.1.1")
    assert (i.tipo_ip, i.pais, i.ciudad_estimada, i.es_datacenter, i.reputacion) == (
        "RESIDENCIAL",
        "Colombia",
        "Medellín",
        False,
        80,
    )
    assert i.latitud is None and i.longitud is None  # ciudad poco fiable dentro de Colombia: no se calcula distancia


def test_fuera_de_colombia_si_entrega_coordenadas():
    p = proveedor(
        {"5.5.5.5": reg_pais("NL", "Ámsterdam", 52.3, 4.9, es="Países Bajos")}, {"5.5.5.5": org("PROVEEDOR NORMAL")}
    )
    i = p.consultar("5.5.5.5")
    assert i.pais == "Países Bajos" and (i.latitud, i.longitud) == (52.3, 4.9) and i.tipo_ip == "RESIDENCIAL"


def test_tor_es_proxy_de_centro_de_datos_con_la_peor_reputacion():
    p = proveedor({"6.6.6.6": reg_pais("DE")}, {"6.6.6.6": org("NORMAL")}, tor={"6.6.6.6"})
    i = p.consultar("6.6.6.6")
    assert (i.tipo_ip, i.es_datacenter, i.reputacion) == ("PROXY", True, 20)


def test_un_rango_de_nube_es_centro_de_datos_en_ipv4_e_ipv6_y_el_vecino_fuera_del_rango_no():
    p = proveedor(
        {
            "3.5.140.2": reg_pais("US"),
            "3.5.141.2": reg_pais("US"),
            "2600:1f18::1": reg_pais("US"),
            "2600:1f19::1": reg_pais("US"),
        },
        {k: org("NORMAL") for k in ("3.5.140.2", "3.5.141.2", "2600:1f18::1", "2600:1f19::1")},
        nube=["3.5.140.0/24", "2600:1f18::/32"],
    )
    assert p.consultar("3.5.140.2").es_datacenter and p.consultar("2600:1f18::1").es_datacenter
    assert not p.consultar("3.5.141.2").es_datacenter and not p.consultar("2600:1f19::1").es_datacenter


def test_un_operador_de_alojamiento_conocido_es_centro_de_datos_por_el_nombre_de_su_asn():
    p = proveedor({"7.7.7.7": reg_pais("FR")}, {"7.7.7.7": org("Hetzner Online GmbH")})
    i = p.consultar("7.7.7.7")
    assert (i.tipo_ip, i.es_datacenter, i.reputacion) == ("PROXY", True, 45)


def test_ips_privadas_o_sin_ningun_dato_son_desconocidas_y_no_inventan_pais():
    privadas = ("10.0.0.5", "127.0.0.1", "169.254.1.1", "192.168.1.1", "::1")
    p = proveedor(
        {ip: reg_pais("CO", "Bogotá") for ip in privadas}, {ip: org("RED LOCAL") for ip in privadas}
    )  # aunque alguna base «supiera» algo de ellas, no se les cree
    for ip in privadas:
        assert p.consultar(ip).tipo_ip == "DESCONOCIDA" and p.consultar(ip).pais == "Desconocido"
    i = p.consultar("8.9.9.9")  # pública, pero ninguna base la conoce
    assert (i.tipo_ip, i.pais, i.reputacion, i.es_datacenter) == ("DESCONOCIDA", "Desconocido", 50, False)


def test_los_nombres_de_pais_son_cortos_y_sin_el_articulo_formal():
    assert ip_propia._nombre_pais("US", {"es": "Estados Unidos de América (los)"}) == "Estados Unidos"
    assert (
        ip_propia._nombre_pais("ZZ", {"es": "Tierra del Fuego (la)"}) == "Tierra del Fuego"
        and ip_propia._nombre_pais(None, {}) == "Desconocido"
    )


def test_lo_que_no_sabe_no_lo_afirma_nunca_hay_movil_ni_vpn_comercial():
    """Sin esto la documentación mentiría: la base solo da el nombre del operador."""
    p = proveedor({"190.1.1.1": reg_pais("CO")}, {"190.1.1.1": org("COMCEL S.A.")})
    assert p.consultar("190.1.1.1").tipo_ip not in ("MOVIL", "VPN")
    assert (
        "NO sabe" in ip_propia.__doc__ and "reputación" in ip_propia.__doc__ and "ESTIMACIÓN FIJA" in ip_propia.__doc__
    )


# ------------------------------------------------------------------ con las bases reales (si están descargadas)
reales = pytest.mark.skipif(
    not all((ip_datos.CARPETA / f["archivo"]).exists() for f in ip_datos.FUENTES.values()),
    reason="bases de IP no descargadas",
)


@reales
def test_con_las_bases_reales_google_es_estados_unidos_y_centro_de_datos_y_tor_es_proxy():
    p = crear_proveedor("propio")
    g = p.consultar("8.8.8.8")
    assert g.pais == "Estados Unidos" and g.es_datacenter and g.tipo_ip == "PROXY"
    t = p.consultar(sorted(p._tor)[0])
    assert t.tipo_ip == "PROXY" and t.reputacion == 20 and p.consultar("2001:4860:4860::8888").es_datacenter


@reales
def test_con_las_bases_reales_una_ip_colombiana_da_colombia_sin_coordenadas():
    p = crear_proveedor("propio")
    ip = next(
        str(r.network_address + 5)
        for r, reg in p._ciudad
        if r.version == 4 and (reg.get("country") or {}).get("iso_code") == "CO"
    )
    i = p.consultar(ip)
    assert i.pais == "Colombia" and i.latitud is None and i.longitud is None


# ------------------------------------------------------------------ descargador
def _json_nube(n=150):
    return json.dumps({"prefixes": [{"ip_prefix": f"10.{i}.0.0/16"} for i in range(n)]}).encode()


def _falso(contenido):
    pedidos = []

    def leer(url):
        pedidos.append(url)
        r = contenido(url) if callable(contenido) else contenido
        if isinstance(r, Exception):
            raise r
        return r

    leer.pedidos = pedidos
    return leer


def _tor(n=150):
    return ("\n".join(f"203.0.{i // 250}.{i % 250 + 1}" for i in range(n))).encode()


def _por_url(url):
    return {"aws": _json_nube(), "cloud.json": _json_nube(), "torbulkexitlist": _tor()}[
        ("aws" if "amazonaws" in url else "cloud.json" if "gstatic" in url else "torbulkexitlist")
    ]


def test_descarga_verifica_y_deja_un_manifiesto_con_huella_y_licencia(tmp_path):
    r = ip_datos.descargar(tmp_path, solo=["aws", "google", "tor"], leer=_falso(_por_url))
    assert all(v.startswith("descargado") for v in r.values())
    m = json.loads((tmp_path / "manifiesto.json").read_text())
    assert (
        set(m) == {"aws", "google", "tor"}
        and len(m["tor"]["sha256"]) == 64
        and "abogado" in m["tor"]["licencia"]
        and m["aws"]["comprobado"] == "150 rangos"
    )
    assert not list(tmp_path.glob("*.parte"))


def test_lo_vigente_no_se_vuelve_a_bajar_y_lo_vencido_si(tmp_path):
    leer = _falso(_por_url)
    ip_datos.descargar(tmp_path, solo=["tor"], leer=leer, hoy=date(2026, 11, 1))
    assert (
        ip_datos.descargar(tmp_path, solo=["tor"], leer=leer, hoy=date(2026, 11, 10)) == {"tor": "vigente"}
        and len(leer.pedidos) == 1
    )
    assert json.loads((tmp_path / "manifiesto.json").read_text())["tor"]["descargado"].startswith(
        "2026-11-01"
    )  # la fecha es la de la llamada, no la del reloj real
    assert (
        ip_datos.descargar(tmp_path, solo=["tor"], leer=leer, hoy=date(2026, 12, 20))["tor"].startswith("descargado")
        and len(leer.pedidos) == 2
    )


def test_un_archivo_daniado_no_reemplaza_al_bueno(tmp_path):
    ip_datos.descargar(tmp_path, solo=["tor"], leer=_falso(_por_url), hoy=date(2026, 11, 1))
    bueno = (tmp_path / "tor-salidas.txt").read_bytes()
    for malo in (b"esto no es una lista", b"1.2.3.4\n" * 3, b"\xff\xfe basura"):
        r = ip_datos.descargar(tmp_path, solo=["tor"], leer=_falso(malo), hoy=date(2027, 3, 1))
        assert r["tor"].startswith("FALLÓ") and "se conserva la versión anterior" in r["tor"]
        assert (tmp_path / "tor-salidas.txt").read_bytes() == bueno and not list(tmp_path.glob("*.parte"))


def test_si_la_red_falla_se_conserva_lo_anterior_y_otras_fuentes_siguen(tmp_path):
    def contenido(url):
        return OSError("sin red") if "gstatic" in url else _por_url(url)

    r = ip_datos.descargar(tmp_path, solo=["aws", "google", "tor"], leer=_falso(contenido))
    assert (
        r["google"].startswith("FALLÓ")
        and "no hay versión anterior" in r["google"]
        and r["aws"].startswith("descargado")
        and r["tor"].startswith("descargado")
    )
    assert not (tmp_path / "google-cloud.json").exists()


def test_la_base_mensual_prueba_el_mes_actual_y_cae_al_anterior(tmp_path):
    antes = ip_datos.FUENTES["ciudad"]
    leer = _falso(lambda url: OSError("404") if "2026-11" in url else b"")
    r = ip_datos.descargar(tmp_path, solo=["ciudad"], leer=leer, hoy=date(2026, 11, 14))
    assert [u.rsplit("-", 2)[-2] + "-" + u.rsplit("-", 1)[-1][:2] for u in leer.pedidos] == [
        "2026-11",
        "2026-10",
    ] and r["ciudad"].startswith("FALLÓ")
    assert ip_datos._meses(date(2027, 1, 5)) == ["2027-01", "2026-12"] and antes["mensual"]


def test_no_baja_por_http_ni_archivos_gigantes(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        ip_datos._leer("http://ejemplo.com/x")
    with pytest.raises(ValueError, match="HTTPS"):
        ip_datos._leer("file:///etc/passwd")
    assert all(f["url"].startswith("https://") for f in ip_datos.FUENTES.values()) and ip_datos.TOPE_MB <= 200


def test_estado_dice_que_falta_y_detecta_lo_daniado(tmp_path):
    assert set(ip_datos.estado(tmp_path).values()) == {"falta"}
    ip_datos.descargar(tmp_path, solo=["tor"], leer=_falso(_por_url))
    (tmp_path / "tor-salidas.txt").write_text("basura")
    assert ip_datos.estado(tmp_path)["tor"].startswith("DAÑADO")


def test_sin_las_bases_el_proveedor_propio_se_niega_a_arrancar_con_un_mensaje_claro(tmp_path):
    with pytest.raises(ValueError, match="ip_datos descargar"):
        ProveedorPropio(tmp_path)
    with pytest.raises(ValueError, match="Opciones"):
        crear_proveedor("inventado")


# ------------------------------------------------------------------ en la API
def test_la_api_con_el_proveedor_propio_completa_la_red_y_nunca_guarda_la_ip(tmp_path, artefacto):  # noqa: F811
    prov = proveedor(
        {"5.5.5.5": reg_pais("NL", "Ámsterdam", 52.3, 4.9, es="Países Bajos")}, {"5.5.5.5": org("Hetzner")}
    )
    prov.nombre = "propio"
    app = crear_app(
        Ajustes(ruta_bd=tmp_path / "p.sqlite3", pepper="p" * 40), artefacto, reloj=lambda: RELOJ, proveedor_ip=prov
    )
    clave = app.state.almacen.crear_clave(app.state.almacen.crear_comercio("t"), "comercio")
    r = TestClient(app).post("/v1/evaluaciones", json=con_ip("PR1", "CPR", "5.5.5.5"), headers={"X-API-Key": clave})
    assert r.status_code == 201 and r.json()["red_origen"] == "proveedor:propio" and "5.5.5.5" not in r.text
    f = json.loads(sqlite3.connect(tmp_path / "p.sqlite3").execute("SELECT fila_json FROM evaluaciones").fetchone()[0])
    assert (f["tipo_ip"], f["ip_pais"], f["ip_es_datacenter"], f["ip_reputacion"]) == ("Proxy", "Países Bajos", 1, 45)
    assert b"5.5.5.5" not in b"".join(x.read_bytes() for x in tmp_path.glob("p.sqlite3*"))
    assert not any("5.5.5.5" in str(v) for v in f.values())


def test_la_antiguedad_de_las_bases_genera_un_aviso(tmp_path, artefacto):  # noqa: F811
    prov = proveedor()
    prov.nombre, prov.antiguedad_dias = "propio", 90
    app = crear_app(
        Ajustes(ruta_bd=tmp_path / "v.sqlite3", pepper="p" * 40), artefacto, reloj=lambda: RELOJ, proveedor_ip=prov
    )
    assert any("90 días" in a for a in app.state.servicio.advertencias)
    prov.antiguedad_dias = 5
    assert not any("días" in a and "bases de IP" in a for a in app.state.servicio.advertencias)


# ------------------------------------------------------------------ crédito de DB-IP y documentación
def test_el_credito_de_db_ip_esta_en_las_pantallas_que_muestran_la_ubicacion_y_en_la_documentacion():
    from modelo_ia.api import documentacion

    base = documentacion.FRONTEND
    for pantalla in ("panel", "dashboard"):
        html = (base / pantalla / "index.html").read_text(encoding="utf-8")
        assert 'href="https://db-ip.com"' in html and "IP Geolocation by DB-IP" in html and "pie-creditos" in html
    assert ".pie-creditos" in (base / "comun" / "diseno.css").read_text(encoding="utf-8")
    raiz = base.parent  # modelo_ia/
    assert (
        "db-ip.com" in (raiz / "README.md").read_text(encoding="utf-8")
        and "CC BY 4.0"
        in json.loads((raiz / "api" / "openapi.json").read_text(encoding="utf-8"))["info"]["description"]
    )


def test_la_respuesta_de_una_evaluacion_nunca_devuelve_la_ubicacion_de_la_ip(tmp_path, artefacto):  # noqa: F811
    """Por eso el crédito va en las pantallas y no en cada respuesta: lo que dice la documentación debe ser verdad."""
    prov = proveedor(
        {"5.5.5.5": reg_pais("NL", "Ámsterdam", 52.3, 4.9, es="Países Bajos")}, {"5.5.5.5": org("Hetzner")}
    )
    app = crear_app(
        Ajustes(ruta_bd=tmp_path / "r.sqlite3", pepper="p" * 40), artefacto, reloj=lambda: RELOJ, proveedor_ip=prov
    )
    clave = app.state.almacen.crear_clave(app.state.almacen.crear_comercio("t"), "comercio")
    cuerpo = (
        TestClient(app)
        .post("/v1/evaluaciones", json=con_ip("PR2", "CPR2", "5.5.5.5"), headers={"X-API-Key": clave})
        .text
    )
    for dato in ("Ámsterdam", "Países Bajos", "52.3", "Hetzner", "ip_pais"):
        assert dato not in cuerpo


def test_la_unica_direccion_externa_permitida_en_las_pantallas_es_el_credito_de_db_ip():
    """Si alguien agrega otro enlace o recurso externo, las pruebas de seguridad de las pantallas deben seguir fallando."""
    from modelo_ia.tests.test_api import CREDITO_DB_IP, compacto, sin_credito
    from modelo_ia.api import documentacion

    for pantalla in ("panel", "dashboard"):
        html = (documentacion.FRONTEND / pantalla / "index.html").read_text(encoding="utf-8")
        assert CREDITO_DB_IP in compacto(html) and "https://" not in sin_credito(html)
        otro = html.replace("</body>", '<a href="https://otro.example">x</a></body>')
        assert "https://" in sin_credito(otro)  # un segundo enlace externo NO queda cubierto por la excepción
        assert "https://" in sin_credito(
            html.replace("noopener noreferrer", "noopener")
        )  # ni el mismo enlace sin su rel


def test_si_la_base_trae_algo_inesperado_se_trata_como_vacio_y_no_falla():
    """Un archivo dañado o de otra versión no debe tumbar la evaluación: la IP queda como desconocida."""
    extraños = [[1, 2, 3], 42, "texto", {"country": [1], "city": 5, "location": "x"}, {"country": {"names": 7}}]
    for raro in extraños:
        p = proveedor({"6.6.6.6": raro}, {"6.6.6.6": raro})
        i = p.consultar("6.6.6.6")
        assert i.tipo_ip == "DESCONOCIDA" and i.pais == "Desconocido" and i.reputacion == 50, (raro, i)
