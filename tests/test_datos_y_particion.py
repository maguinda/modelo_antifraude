import pandas as pd
import pytest

from modelo_ia import config, datos, particion


def test_validar_esquema_falta_columna(df_crudo):
    with pytest.raises(ValueError, match="Faltan columnas"):
        datos.validar_esquema(df_crudo.drop(columns=["tipo_ip"]))


def test_validar_esquema_es_fraude_solo_0_o_1(df_crudo):
    df_crudo.loc[0, "es_fraude"] = 2
    with pytest.raises(ValueError, match="0 o 1"):
        datos.validar_esquema(df_crudo)


def test_validar_esquema_sin_fraudes(df_crudo):
    df_crudo["es_fraude"] = 0
    with pytest.raises(ValueError, match="ningún fraude"):
        datos.validar_esquema(df_crudo)


def test_validar_esquema_monto_invalido(df_crudo):
    df_crudo.loc[0, "monto_cop"] = 0
    with pytest.raises(ValueError, match="montos"):
        datos.validar_esquema(df_crudo)


def test_cargar_deja_orden_de_fecha_y_columnas_basicas(ruta_csv):
    df = datos.cargar(ruta_csv)
    assert df["fecha"].is_monotonic_increasing
    assert {"mes", "hora", "categoria"} <= set(df.columns)
    assert set(df["categoria"]) <= {"Ropa", "Hotel", "Farmacia", "Electrónica"}


def test_cargar_archivo_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        datos.cargar(tmp_path / "no_existe.csv")


def test_particion_no_se_mezcla_y_respeta_el_orden_en_el_tiempo(ruta_csv):
    df = datos.cargar(ruta_csv)
    m = particion.dividir(df)
    assert not (m["entrenamiento"] & m["validacion"]).any()
    assert not (m["validacion"] & m["prueba"]).any()
    assert not (m["entrenamiento"] & m["prueba"]).any()
    f = df["fecha"]
    assert f[m["entrenamiento"]].max() < f[m["validacion"]].min()
    assert f[m["validacion"]].max() < f[m["prueba"]].min()


def test_particion_respeta_las_fechas_de_corte(ruta_csv):
    df = datos.cargar(ruta_csv)
    m = particion.dividir(df)
    f = df["fecha"]
    assert f[m["entrenamiento"]].max() < pd.Timestamp("2026-07-01")
    assert pd.Timestamp("2026-07-01") <= f[m["validacion"]].min()
    assert f[m["validacion"]].max() < pd.Timestamp("2026-09-01")
    assert f[m["prueba"]].min() >= pd.Timestamp("2026-09-01")


def test_particion_con_dos_anos_no_mezcla_2025_con_2026(df_crudo):
    """Con datos de 2025 y 2026, un mismo mes de años distintos NO puede caer en conjuntos distintos por error."""
    d = df_crudo.copy()
    antes = d.copy()
    antes["fecha_pago"] = (pd.to_datetime(antes["fecha_pago"]) - pd.DateOffset(years=1)).dt.strftime("%Y-%m-%d %H:%M:%S")
    todo = datos.agregar_columnas_basicas(pd.concat([antes, d], ignore_index=True).sort_values("fecha_pago"))
    m = particion.dividir(todo)
    assert (todo.loc[m["entrenamiento"], "fecha"].dt.year == 2025).any()          # todo 2025 entrena
    assert (todo.loc[m["prueba"], "fecha"].dt.year == 2026).all()
    assert (todo.loc[todo["fecha"].dt.year == 2025].index.isin(todo.index[m["entrenamiento"]])).all()


def test_particion_falla_si_los_cortes_estan_en_desorden(ruta_csv, monkeypatch):
    df = datos.cargar(ruta_csv)
    monkeypatch.setattr(config, "FIN_VALIDACION", "2026-05-01")
    with pytest.raises(ValueError, match="desorden"):
        particion.dividir(df)


def test_cargar_lee_un_csv_comprimido(tmp_path, df_crudo):
    ruta = tmp_path / "datos.csv.gz"
    df_crudo.to_csv(ruta, index=False, encoding="utf-8-sig", compression="gzip")
    df = datos.cargar(ruta)
    assert len(df) == len(df_crudo) and df["fecha"].is_monotonic_increasing


def test_las_columnas_de_despues_del_pago_estan_prohibidas_como_entrada():
    """Revisión manual y contracargos se saben DESPUÉS del pago: usarlas sería mirar el futuro."""
    despues = ["revision_manual", "resultado_revision", "minutos_revision", "contracargo",
               "dias_hasta_contracargo", "fecha_confirmacion_fraude"]
    identificadores = ["id_comercio", "huella_dispositivo"]
    assert set(despues + identificadores) <= set(config.COLUMNAS_PROHIBIDAS)
