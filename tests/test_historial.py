"""Adaptador de historial: catálogo, mapeo, conversión, selección de modelo y el recorrido completo con un archivo inventado."""
import csv
import json
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from modelo_ia import config
from modelo_ia.historial import campos, conversion, ejemplo, informe, lectura, mapeo, seleccion
from modelo_ia.historial.__main__ import main

MAPEO_BASE = {"formato": dict(mapeo.FORMATO_POR_DEFECTO, decimal=","), "valores": {}, "por_defecto": {}, "resultado": {"columna": "malo", "fraude": ["1"], "legitima": ["0"]},
              "columnas": {"id_transaccion": "pedido", "id_cliente": "cliente", "fecha_pago": "fecha", "monto_cop": "valor", "metodo_pago": "medio", "canal_pago": "canal",
                           "categoria": "cat", "ciudad": "ciudad", "dispositivo.tipo": "equipo", "red.ip": "ip"}}
FILA = {"pedido": "PED-1", "cliente": "ana@correo.com", "fecha": "2026-03-05 14:30:00", "valor": "$ 1.250.000", "medio": "visa", "canal": "web", "cat": "ropa", "ciudad": "Bogotá",
        "equipo": "iphone", "ip": "192.0.2.10", "malo": "0"}


def conv(mapeo_extra=None, seud=True):
    m = json.loads(json.dumps(MAPEO_BASE))
    for k, v in (mapeo_extra or {}).items():
        m[k].update(v) if isinstance(m[k], dict) else m.__setitem__(k, v)
    return conversion.Convertidor(m, seud)


# ------------------------------------------------------------------ catálogo, sugerencias y verificación
def test_el_catalogo_cubre_todo_el_contrato_de_la_api():
    api, cat = set(campos.rutas_de_la_api()) - {"moneda"}, set(campos.POR_RUTA)
    assert api == cat, (api - cat, cat - api)                     # si la API gana un campo, este catálogo debe enterarse
    assert {c.ruta for c in campos.CAMPOS if c.opciones} >= {"metodo_pago", "canal_pago", "categoria", "dispositivo.tipo"}


def test_normalizar_quita_tildes_mayusculas_y_signos():
    assert campos.normalizar("  Tarjeta_Crédito-VISA ") == "tarjeta credito visa" and campos.normalizar("Ciudad Entrega") == "ciudad entrega"


def test_sugerir_reconoce_nombres_comunes_y_no_inventa():
    m = mapeo.sugerir(["Order ID", "customer_id", "created_at", "Amount", "payment method", "IP Address", "Chargeback", "columna_rara"])
    c = {k: v for k, v in m["columnas"].items() if v}
    assert c["id_transaccion"] == "Order ID" and c["id_cliente"] == "customer_id" and c["fecha_pago"] == "created_at" and c["monto_cop"] == "Amount"
    assert c["metodo_pago"] == "payment method" and c["red.ip"] == "IP Address" and m["resultado"]["columna"] == "Chargeback"
    assert "columna_rara" not in c.values() and len(set(c.values())) == len(c)             # ninguna columna se usa dos veces


def test_verificar_marca_lo_que_falta_y_avisa_de_lo_que_empobrece(tmp_path):
    base = mapeo.cargar(_guardar(tmp_path, MAPEO_BASE))
    r = mapeo.verificar(base, list(FILA))
    assert r["errores"] == [] and any("sin columna" in a for a in r["avisos"])
    sin_monto = json.loads(json.dumps(MAPEO_BASE)); del sin_monto["columnas"]["monto_cop"]
    assert any("monto_cop" in e for e in mapeo.verificar(mapeo.cargar(_guardar(tmp_path, sin_monto)), list(FILA))["errores"])
    sin_red = json.loads(json.dumps(MAPEO_BASE)); del sin_red["columnas"]["red.ip"]
    assert any("red" in e for e in mapeo.verificar(mapeo.cargar(_guardar(tmp_path, sin_red)), list(FILA))["errores"])
    assert any("«fecha»" in e or "fecha" in e for e in mapeo.verificar(base, ["pedido"])["errores"])             # columnas que no existen en el archivo
    fijo = json.loads(json.dumps(MAPEO_BASE)); del fijo["columnas"]["canal_pago"]; fijo["por_defecto"] = {"canal_pago": "WEB"}
    assert any("valor fijo" in a for a in mapeo.verificar(mapeo.cargar(_guardar(tmp_path, fijo)), list(FILA))["avisos"])
    with pytest.raises(mapeo.ErrorDeMapeo):
        mapeo.cargar(_guardar(tmp_path, dict(MAPEO_BASE, columnas={"campo_que_no_existe": "x"})))


def _guardar(tmp_path, m):
    p = tmp_path / f"m{len(list(tmp_path.iterdir()))}.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    return p


# ------------------------------------------------------------------ conversión de filas
def test_una_fila_normal_se_convierte_a_la_api():
    f = conv().convertir(2, dict(FILA))
    assert f.motivo is None and f.solicitud["monto_cop"] == 1250000 and f.solicitud["metodo_pago"] == "VISA" and f.solicitud["canal_pago"] == "WEB"
    assert f.solicitud["categoria"] == "ROPA" and f.solicitud["dispositivo"]["tipo"] == "IPHONE" and f.solicitud["red"]["ip"] == "192.0.2.10"
    assert f.fecha == datetime(2026, 3, 5, 14, 30) and f.fraude is False and f.monto == 1250000


@pytest.mark.parametrize("texto,decimal,esperado", [("$ 1.250.000", ",", 1250000), ("1250000", ".", 1250000), ("1,250.50", ".", 1251), ("125.000,50", ",", 125001), ("80000.0", ".", 80000)])
def test_los_montos_se_leen_con_cualquier_formato(texto, decimal, esperado):
    f = conv({"formato": {"decimal": decimal}}).convertir(2, dict(FILA, valor=texto))
    assert f.motivo is None and f.monto == esperado


def test_la_escala_del_monto_y_los_montos_malos():
    assert conv({"formato": {"escala_monto": 0.01}}).convertir(2, dict(FILA, valor="12500000")).monto == 125000      # montos guardados en centavos
    for malo in ("", "gratis", "0", "-5"):
        assert conv().convertir(2, dict(FILA, valor=malo)).motivo in ("monto no válido", "falta monto_cop"), malo


@pytest.mark.parametrize("texto,formato,esperado", [("05/03/2026 14:30", None, datetime(2026, 3, 5, 14, 30)), ("2026-03-05T14:30:00", None, datetime(2026, 3, 5, 14, 30)),
                                                    ("5/3/2026", "%d/%m/%Y", datetime(2026, 3, 5)), ("2026-03-05T19:30:00Z", None, datetime(2026, 3, 5, 14, 30))])
def test_las_fechas_se_leen_en_varios_formatos_y_zonas(texto, formato, esperado):
    f = conv({"formato": {"formato_fecha": formato}}).convertir(2, dict(FILA, fecha=texto))
    assert f.motivo is None and f.fecha == esperado


def test_fechas_en_utc_se_pasan_a_hora_de_bogota_y_las_malas_se_rechazan():
    assert conv({"formato": {"zona_horaria_origen": "UTC"}}).convertir(2, dict(FILA, fecha="2026-03-05 19:30:00")).fecha == datetime(2026, 3, 5, 14, 30)
    assert conv().convertir(2, dict(FILA, fecha="ayer")).motivo == "fecha no válida"


def test_los_textos_del_comercio_se_traducen_y_los_que_no_se_cuentan_sin_repetir_clientes():
    c = conv({"valores": {"metodo_pago": {"tarjeta credito visa": "VISA"}}})
    assert c.convertir(2, dict(FILA, medio="Tarjeta crédito Visa")).solicitud["metodo_pago"] == "VISA"
    r = c.convertir(3, dict(FILA, medio="cripto raro"))
    assert r.motivo == "valor no reconocido en metodo_pago" and "cripto raro" not in r.motivo and c.valores_sin_traducir["metodo_pago"] == {"cripto raro": 1}


def test_los_ids_se_vuelven_codigos_solo_de_letras_estables_en_la_ejecucion_y_distintos_entre_ejecuciones():
    a, b = conv(), conv()
    x, y = a.convertir(2, dict(FILA)), a.convertir(3, dict(FILA))
    s = x.solicitud
    assert "ana@" not in json.dumps(s) and "correo" not in json.dumps(s) and s["id_cliente"] != "ana@correo.com" and s["id_transaccion"] != "PED-1"
    assert s["id_cliente"] == y.solicitud["id_cliente"] and s["id_cliente"] != b.convertir(2, dict(FILA)).solicitud["id_cliente"]
    assert not re.search(r"\d", s["id_cliente"] + s["id_transaccion"])                       # sin dígitos: no pueden parecer un número de tarjeta
    assert re.match(r"^[A-Za-z0-9_\-:.]{1,64}$", s["id_cliente"])


def test_sin_seudonimos_los_ids_se_mantienen():
    s = conv(seud=False).convertir(2, dict(FILA, cliente="CLI-77")).solicitud
    assert s["id_cliente"] == "CLI-77" and s["id_transaccion"] == "PED-1"


def test_cientos_de_codigos_nunca_son_rechazados_por_parecer_tarjetas():
    c = conv()
    rechazadas = [n for n in range(2000) if c.convertir(n, dict(FILA, pedido=f"P{n}", cliente=f"U{n}")).motivo]
    assert rechazadas == []                                   # con hexadecimal esto fallaba por casualidad en ~0,3 % de las filas


def test_un_numero_de_tarjeta_se_rechaza_y_el_motivo_no_lo_repite():
    f = conv().convertir(2, dict(FILA, ciudad="4111 1111 1111 1111"))
    assert f.solicitud is None and f.motivo == "contiene un número de tarjeta" and not re.search(r"\d{4}", f.motivo)


def test_una_ip_mala_se_descarta_pero_sin_ninguna_red_la_fila_no_sirve():
    c = conv({"columnas": {"red.tipo_ip": "tipo"}, "valores": {"red.tipo_ip": {"casa": "RESIDENCIAL"}}})
    f = c.convertir(2, dict(FILA, ip="no es una ip", tipo="casa"))
    assert f.solicitud and "ip" not in f.solicitud["red"] and f.solicitud["red"]["tipo_ip"] == "RESIDENCIAL" and "red.ip" in f.avisos
    assert conv().convertir(2, dict(FILA, ip="no es una ip")).motivo == "sin datos de red utilizables"


def test_faltas_obligatorias_y_etiquetas():
    assert conv().convertir(2, dict(FILA, ciudad="")).motivo == "falta ciudad"
    assert conv().convertir(2, dict(FILA, malo="1")).fraude is True and conv().convertir(2, dict(FILA, malo="")).fraude is None
    assert conv({"por_defecto": {"ciudad": "Bogotá"}}).convertir(2, dict(FILA, ciudad="")).solicitud["ciudad"] == "Bogotá"


# ------------------------------------------------------------------ lectura de archivos
def test_lee_csv_con_otros_separadores_y_codificaciones(tmp_path):
    for sep in (",", ";", "\t", "|"):
        p = tmp_path / "a.csv"
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=sep); w.writerow(["pedido", "ciudad"]); w.writerow(["1", "Medellín"])
        assert list(lectura.leer(p, {"separador": "auto"}))[0] == (2, {"pedido": "1", "ciudad": "Medellín"})
    p.write_bytes("pedido;ciudad\n1;Medellín\n".encode("latin-1"))
    with pytest.raises(lectura.ErrorDeArchivo, match="latin-1"):
        list(lectura.leer(p, {"separador": ";", "codificacion": "utf-8"}))
    assert list(lectura.leer(p, {"separador": ";", "codificacion": "latin-1"}))[0][1]["ciudad"] == "Medellín"
    with pytest.raises(lectura.ErrorDeArchivo):
        list(lectura.leer(tmp_path / "no_existe.csv", {}))


def test_lee_excel(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    libro = openpyxl.Workbook(); h = libro.active
    h.append(["pedido", "fecha", "valor"]); h.append(["1", datetime(2026, 3, 5, 14, 30), 1250000]); h.append([None, None, None])
    p = tmp_path / "a.xlsx"; libro.save(p)
    filas = list(lectura.leer(p, {}))
    assert filas == [(2, {"pedido": "1", "fecha": "2026-03-05 14:30:00", "valor": "1250000"})]
    with pytest.raises(lectura.ErrorDeArchivo):
        list(lectura.leer(tmp_path / "viejo.xls", {}))


# ------------------------------------------------------------------ elección del modelo según los datos
def _cob(**cambios):
    base = {c.ruta: 1.0 for c in campos.CAMPOS}
    base.update(cambios)
    return base


def test_con_todos_los_datos_se_elige_el_modelo_completo_y_con_pocos_uno_pequeno():
    assert seleccion.elegir(_cob(**{"dispositivo.estado": 0.0}))["candidato"]["conjunto"] == "completo"
    pobre = _cob(**{k: 0.0 for k in campos.POR_RUTA if k.split(".")[0] in ("identidad", "comportamiento") or k.startswith("dispositivo.") and k != "dispositivo.tipo"})
    pobre["red.pais"] = pobre["red.es_datacenter"] = pobre["red.reputacion"] = 0.0
    e = seleccion.elegir(pobre)
    assert e["candidato"]["conjunto"] in ("perfil", "historial") and e["descartados"] and e["candidato"]["sin_estado"] is True


def test_se_necesitan_los_datos_minimos_y_se_puede_forzar_un_nivel():
    with pytest.raises(ValueError, match="Ningún modelo"):
        seleccion.elegir(_cob(**{"red.ip": 0.0, "red.tipo_ip": 0.0}))
    with pytest.raises(ValueError):
        seleccion.elegir(_cob(), "no_existe")
    assert seleccion.elegir(_cob(), "historial")["candidato"]["conjunto"] == "historial"


def test_un_campo_con_poca_cobertura_no_cuenta_como_disponible():
    casi = _cob(**{"identidad.autenticacion_2fa": 0.5, "identidad.intentos_fallidos_24h": 0.5, "identidad.cambio_credenciales_7d": 0.5})
    assert seleccion.elegir(casi)["candidato"]["conjunto"] != "completo"


# ------------------------------------------------------------------ medición
def test_wilson_da_intervalos_razonables():
    assert informe.wilson(0, 0) is None
    lo, hi = informe.wilson(80, 100)
    assert 0.70 < lo < 0.80 < hi < 0.88
    assert informe.wilson(100, 100)[1] == 1.0 and informe.wilson(0, 30)[0] == 0.0


# ------------------------------------------------------------------ de punta a punta con un archivo inventado
@pytest.fixture(scope="module")
def corrida(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("hist")
    r = ejemplo.generar(carpeta, n=700, semilla=5)
    antes = set(Path(tempfile.gettempdir()).glob("historial_*"))
    real = config.RAIZ / "api" / "datos_api.sqlite3"
    marca_real = real.stat().st_mtime_ns if real.exists() else None
    salida = carpeta / "salida"
    assert main(["evaluar", str(r["archivo"]), "--mapeo", str(r["mapeo"]), "--salida", str(salida), "--madurez-dias", "30"]) == 0
    return {"carpeta": carpeta, "archivo": r["archivo"], "mapeo": r["mapeo"], "salida": salida, "antes": antes, "real": real, "marca_real": marca_real,
            "informe": json.loads((salida / "informe.json").read_text(encoding="utf-8")),
            "filas": list(csv.DictReader(open(salida / "resultados.csv", encoding="utf-8-sig")))}


def test_el_recorrido_completo_deja_el_informe_y_el_detalle(corrida):
    i = corrida["informe"]
    assert i["datos"]["filas_leidas"] == 700
    assert i["datos"]["filas_evaluadas"] == sum(1 for f in corrida["filas"] if f["decision"]) and i["datos"]["filas_evaluadas"] + i["datos"]["filas_rechazadas_al_leer"] <= 700
    assert i["datos"]["motivos_de_rechazo"]  # el archivo trae filas malas a propósito
    assert i["modelo"]["conjunto"] in ("perfil", "historial") and i["modelo"]["grupos_no_usados"]
    assert i["medicion"]["fraudes_en_la_medicion"] > 0 and 0 <= i["medicion"]["revisar_o_rechazar"]["recall"] <= 1
    assert (corrida["salida"] / "informe.md").read_text(encoding="utf-8").startswith("# Diagnóstico de fraude con tu historial")


def test_los_numeros_del_informe_se_pueden_recalcular_desde_el_detalle(corrida):
    i, filas = corrida["informe"], corrida["filas"]
    corte = datetime.fromisoformat(i["medicion"]["corte_de_madurez"] + "T23:59:59")
    maduros = [f for f in filas if f["decision"] and f["resultado_real"] and datetime.fromisoformat(f["fecha"]) <= corte]
    fraudes = [f for f in maduros if f["resultado_real"] == "fraude"]
    detectados = [f for f in fraudes if f["decision"] != "APROBADA"]
    m = i["medicion"]["revisar_o_rechazar"]
    assert len(fraudes) == i["medicion"]["fraudes_en_la_medicion"] and len(detectados) == m["fraudes_detectados"]
    assert m["falsas_alarmas"] == sum(1 for f in maduros if f["resultado_real"] == "legitimo" and f["decision"] != "APROBADA")
    assert m["recall"] == round(len(detectados) / len(fraudes), 4)
    assert m["costo_sin_sistema_cop"] == sum(int(f["monto_cop"]) for f in fraudes)


def test_los_pagos_recientes_no_se_miden_y_se_avisa(corrida):
    i = corrida["informe"]
    assert i["medicion"]["pagos_maduros_medidos"] < i["medicion"]["pagos_con_resultado"] and any("últimos 30 días" in a for a in i["advertencias"])


def test_nada_personal_sale_en_los_resultados(corrida):
    textos = "".join(p.read_text(encoding="utf-8-sig") for p in (corrida["salida"] / n for n in ("resultados.csv", "informe.json", "informe.md")))
    original = corrida["archivo"].read_text(encoding="utf-8-sig")
    ids = set(re.findall(r"PED-\d+", original)) | set(re.findall(r"CLI-[A-Z]?\d+", original))
    ips = set(re.findall(r"\b(?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d+\b", original))
    assert ids and ips and not any(x in textos for x in ids) and not any(x in textos for x in ips)
    assert not re.search(r"\b\d{13,19}\b", textos)


def test_no_queda_ninguna_base_temporal_ni_se_toca_la_base_real(corrida):
    despues = set(Path(tempfile.gettempdir()).glob("historial_*"))
    assert despues - corrida["antes"] == set()
    real = corrida["real"]
    assert (real.stat().st_mtime_ns if real.exists() else None) == corrida["marca_real"]            # la base real ni se abrió para escribir


def test_el_orden_de_las_filas_del_archivo_no_cambia_el_resultado(corrida, tmp_path):
    filas = list(csv.reader(open(corrida["archivo"], encoding="utf-8-sig"), delimiter=";"))
    cab, cuerpo = filas[0], filas[1:]
    inv = tmp_path / "invertido.csv"
    with open(inv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(cab); w.writerows(reversed(cuerpo))
    sal = tmp_path / "s"
    assert main(["evaluar", str(inv), "--mapeo", str(corrida["mapeo"]), "--salida", str(sal), "--madurez-dias", "30"]) == 0
    clave = lambda fs: sorted((f["fecha"], f["monto_cop"], f["decision"], f["puntaje_riesgo"]) for f in fs if f["decision"])
    assert clave(csv.DictReader(open(sal / "resultados.csv", encoding="utf-8-sig"))) == clave(corrida["filas"])


def test_un_mapeo_con_errores_no_evalua_nada(corrida, tmp_path, capsys):
    malo = json.loads(corrida["mapeo"].read_text(encoding="utf-8")); malo["columnas"]["monto_cop"] = "columna_que_no_existe"
    p = tmp_path / "malo.json"; p.write_text(json.dumps(malo), encoding="utf-8")
    assert main(["evaluar", str(corrida["archivo"]), "--mapeo", str(p), "--salida", str(tmp_path / "x")]) == 2
    assert "ERROR" in capsys.readouterr().out and not (tmp_path / "x").exists()


def test_la_linea_de_comandos_imprime_conteos_y_no_valores_de_clientes(corrida, tmp_path, capsys):
    main(["verificar", str(corrida["archivo"]), "--mapeo", str(corrida["mapeo"])])
    salida = capsys.readouterr().out
    assert "usables" in salida and not re.search(r"CLI-|PED-|192\.0\.2\.|@", salida)
