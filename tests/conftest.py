"""Datos pequeños inventados para probar el código sin depender del archivo grande."""
import numpy as np
import pandas as pd
import pytest

from modelo_ia import config


def hacer_datos(n: int = 6000, semilla: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    fechas = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 315 * 24 * 3600, n), unit="s")
    anonima = rng.random(n) < 0.08
    tipo_ip = np.where(anonima, rng.choice(["VPN", "Proxy", "IP desconocida"], n),
                       rng.choice(["Residencial", "Móvil", "Empresarial"], n))
    estado = rng.choice(["Conocido", "Nuevo", "No reconocido"], n, p=[0.90, 0.08, 0.02])
    fraude = ((anonima & (rng.random(n) < 0.5)) | ((estado != "Conocido") & (rng.random(n) < 0.4))).astype(int)
    canal = rng.choice(["App móvil", "API", "POS", "Web"], n)
    categoria = rng.choice(["Ropa", "Hotel", "Farmacia", "Electrónica"], n)
    df = pd.DataFrame({
        "numero_transaccion": [f"TXN-{i:06d}" for i in range(n)],
        "fecha_pago": pd.Series(fechas).dt.strftime("%Y-%m-%d %H:%M:%S"),
        "dispositivo": rng.choice(["iPhone", "Android", "Windows PC", "MacBook"], n),
        "localizacion": rng.choice(["Bogotá, Colombia", "Cali, Colombia", "Medellín, Colombia"], n),
        "monto_cop": np.maximum(1000, rng.lognormal(12.5, 1.0, n).astype(int)),
        "detalle_pago": [f"{c} - compra realizada por {k}" for c, k in zip(categoria, canal)],
        "metodo_pago": rng.choice(["Visa", "Nequi", "PSE"], n),
        "canal_pago": canal,
        "tipo_ip": tipo_ip,
        "estado_dispositivo": estado,
        "transacciones_ultimas_24h": rng.integers(1, 4, n),
        "nivel_riesgo": "Bajo",
        "es_fraude": fraude,
        "id_cliente": [f"CLI-{i:04d}" for i in rng.integers(0, 300, n)],
        "patron": np.where(fraude == 1, "toma_cuenta", "normal"),
    })
    return df


@pytest.fixture
def df_crudo():
    return hacer_datos()


@pytest.fixture
def ruta_csv(tmp_path, df_crudo):
    ruta = tmp_path / "datos.csv"
    df_crudo.to_csv(ruta, index=False, encoding="utf-8-sig")
    return ruta


@pytest.fixture
def carpeta_temporal(tmp_path, monkeypatch):
    """Redirige los archivos que escribe el código a una carpeta temporal."""
    monkeypatch.setattr(config, "CARPETA_MODELOS", tmp_path / "modelos")
    monkeypatch.setattr(config, "ARCHIVO_EXPERIMENTOS", tmp_path / "resultados" / "experimentos.csv")
    return tmp_path


def hacer_datos_completos(n: int = 6000, semilla: int = 0) -> pd.DataFrame:
    """Los datos pequeños de siempre + las columnas nuevas del dataset de 1 millón (con algo de señal)."""
    df = hacer_datos(n, semilla)
    rng = np.random.default_rng(semilla + 1)
    f = df["es_fraude"].values == 1
    so = {"iPhone": "iOS", "Android": "Android", "Windows PC": "Windows", "MacBook": "macOS"}
    df["huella_dispositivo"] = [f"FP-{c}-{d}" for c, d in zip(df["id_cliente"], df["dispositivo"])]
    granja = f & (rng.random(n) < 0.3)
    df.loc[granja, "huella_dispositivo"] = "FP-granja"
    df["sistema_operativo"] = df["dispositivo"].map(so)
    df["navegador"] = np.where(df["canal_pago"].isin(["API", "POS"]), "No aplica", "Chrome")
    df["resolucion_pantalla"] = "390x844"
    df["zona_horaria_dispositivo"] = np.where(f & (rng.random(n) < 0.2), "Europe/Moscow", "America/Bogota")
    df["es_emulador"] = (f & (rng.random(n) < 0.3)).astype(int)
    df["ip_pais"] = np.where(df["tipo_ip"] == "IP desconocida", "Desconocido", "Colombia")
    df["ip_ciudad_estimada"] = "Bogotá"
    df["distancia_ip_ciudad_km"] = np.where(df["ip_pais"] == "Desconocido", np.nan, rng.integers(0, 50, n) + f * 300)
    df["ip_es_datacenter"] = (df["tipo_ip"].isin(["VPN", "Proxy"])).astype(int)
    df["ip_reputacion"] = np.clip(rng.normal(85, 8, n) - f * 30, 0, 100).astype(int)
    df["kyc_nivel"] = rng.choice(["Completo", "Básico", "Sin verificar"], n, p=[0.6, 0.3, 0.1])
    df["edad_cuenta_dias"] = np.where(f & (rng.random(n) < 0.3), rng.integers(0, 5, n), rng.integers(30, 900, n))
    df["autenticacion_2fa"] = np.where(f & (rng.random(n) < 0.3), "Exigida-fallida", "No exigida")
    df["intentos_fallidos_24h"] = rng.poisson(0.05 + f * 1.0)
    df["cambio_credenciales_7d"] = (rng.random(n) < (0.01 + f * 0.3)).astype(int)
    web = df["canal_pago"].isin(["Web", "App móvil"]).values
    df["tiempo_formulario_seg"] = np.where(web, np.where(f, rng.uniform(2, 15, n), rng.uniform(30, 90, n)), np.nan)
    df["velocidad_tecleo_ms"] = np.where(web, rng.normal(150, 30, n), np.nan)
    df["pego_numero_tarjeta"] = np.where(web, (rng.random(n) < (0.03 + f * 0.5)).astype(float), np.nan)
    df["mouse_natural"] = np.where(df["canal_pago"] == "Web", rng.uniform(0, 1, n), np.nan)
    # lo que pasa después del pago
    df["id_comercio"] = [f"PYME-{i:03d}" for i in rng.integers(1, 20, n)]
    dias = rng.integers(10, 120, n)
    confirmada = f & (rng.random(n) < 0.9)
    fecha = pd.to_datetime(df["fecha_pago"])
    df["fecha_confirmacion_fraude"] = (fecha + pd.to_timedelta(dias, unit="D")).where(confirmada).dt.strftime("%Y-%m-%d %H:%M:%S")
    df["contracargo"] = confirmada.astype(int)
    return df


@pytest.fixture
def df_completo():
    return hacer_datos_completos()


@pytest.fixture
def ruta_csv_completo(tmp_path, df_completo):
    ruta = tmp_path / "completo.csv"
    df_completo.to_csv(ruta, index=False, encoding="utf-8-sig")
    return ruta
