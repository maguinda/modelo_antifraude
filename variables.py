"""Variables nuevas a partir de la hora, el historial del cliente y las columnas de contexto.

Regla de oro: lo que depende del pasado (historial, perfil habitual, huella compartida) se calcula
SOLO con compras anteriores o con la fila actual. Nada de lo que pase después puede entrar en una fila.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import config

SO_DE_DISPOSITIVO = {"iPhone": "iOS", "Android": "Android", "Tablet Android": "Android",
                     "Windows PC": "Windows", "MacBook": "macOS"}
# Zonas horarias que se esperan según el país de la IP (si no coincide, algo no cuadra)
TZ_ESPERADAS = {"Colombia": {"America/Bogota"}, "Estados Unidos": {"America/New_York"},
                "España": {"Europe/Madrid"}, "México": {"America/Mexico_City", "America/Cancun"},
                "Panamá": {"America/Panama"}, "Ecuador": {"America/Guayaquil"},
                "Países Bajos": {"Europe/Amsterdam"}, "Alemania": {"Europe/Berlin"},
                "Rusia": {"Europe/Moscow"}, "Nigeria": {"Africa/Lagos"}, "Vietnam": {"Asia/Ho_Chi_Minh"}}

# Columnas crudas que necesita cada grupo
REQUIERE: Dict[str, List[str]] = {
    "hora": ["hora", "fecha"],
    "historial": ["fecha", "id_cliente", "localizacion", "monto_cop", "dispositivo"],
    "perfil": ["id_cliente", "tipo_ip", "hora"],
    "huella": ["huella_dispositivo", "id_cliente", "sistema_operativo", "navegador", "resolucion_pantalla",
               "zona_horaria_dispositivo", "es_emulador", "dispositivo"],
    "geo_ip": ["ip_pais", "distancia_ip_ciudad_km", "ip_es_datacenter", "ip_reputacion", "zona_horaria_dispositivo"],
    "identidad": ["kyc_nivel", "edad_cuenta_dias", "autenticacion_2fa", "intentos_fallidos_24h", "cambio_credenciales_7d"],
    "comportamiento": ["tiempo_formulario_seg", "velocidad_tecleo_ms", "pego_numero_tarjeta", "mouse_natural"],
}


def _claves(d: pd.DataFrame, columnas: List[str], alcance: Optional[str]) -> List[str]:
    """Columnas por las que se agrupa. Con alcance 'comercio', el comercio va primero: cada comercio tiene su propio historial."""
    alcance = alcance or config.ALCANCE_HISTORIAL
    if alcance not in config.ALCANCES_HISTORIAL:
        raise ValueError(f"Alcance desconocido '{alcance}'. Opciones: {list(config.ALCANCES_HISTORIAL)}")
    if alcance == "comercio":
        if "id_comercio" not in d.columns:
            raise ValueError("El alcance 'comercio' necesita la columna id_comercio.")
        return ["id_comercio"] + columnas
    return columnas


def agregar_hora(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["es_madrugada"] = (d["hora"] <= 5).astype(int)
    d["es_fin_semana"] = (d["fecha"].dt.dayofweek >= 5).astype(int)
    return d


def agregar_historial(df: pd.DataFrame, alcance: Optional[str] = None) -> pd.DataFrame:
    """Agrega las 5 variables de historial (necesita las columnas fecha e id_cliente)."""
    d = df.sort_values("fecha", kind="stable").copy()
    g = d.groupby(_claves(d, ["id_cliente"], alcance))

    d["n_compras_previas"] = g.cumcount()
    minutos = (d["fecha"] - g["fecha"].shift(1)).dt.total_seconds() / 60
    d["minutos_desde_anterior"] = minutos.fillna(config.TOPE_MINUTOS_HISTORIAL).clip(
        upper=config.TOPE_MINUTOS_HISTORIAL)
    d["ciudad_distinta_anterior"] = ((d["localizacion"] != g["localizacion"].shift(1))
                                     & (d["n_compras_previas"] > 0)).astype(int)

    suma_previa = g["monto_cop"].cumsum() - d["monto_cop"]          # sin contar la compra actual
    promedio_previo = suma_previa / d["n_compras_previas"].replace(0, np.nan)
    d["monto_vs_promedio"] = (d["monto_cop"] / promedio_previo).fillna(1).clip(upper=20)

    primera_vez = d.groupby(_claves(d, ["id_cliente", "dispositivo"], alcance)).cumcount() == 0
    d["dispositivo_nuevo_cliente"] = (primera_vez & (d["n_compras_previas"] > 0)).astype(int)
    return d.loc[df.index]          # mismo orden que llegó


def agregar_perfil(df: pd.DataFrame, alcance: Optional[str] = None) -> pd.DataFrame:
    """Perfil habitual del cliente: qué tan común es, para él, esta IP y esta franja horaria.

    Valor entre 0 y 1 (parte de sus compras anteriores que fue igual). Sin historial: 0,5 (neutro).
    """
    d = df.sort_values("fecha", kind="stable").copy()
    n = d["n_compras_previas"].replace(0, np.nan)
    d["_franja"] = d["hora"] // 4
    d["ip_frecuencia_previa"] = (d.groupby(_claves(d, ["id_cliente", "tipo_ip"], alcance)).cumcount() / n).fillna(0.5)
    d["franja_frecuencia_previa"] = (d.groupby(_claves(d, ["id_cliente", "_franja"], alcance)).cumcount() / n).fillna(0.5)
    return d.drop(columns="_franja").loc[df.index]


def agregar_huella(df: pd.DataFrame, alcance: Optional[str] = None) -> pd.DataFrame:
    """Huella del dispositivo: cuántos clientes distintos la han usado y si el equipo cuadra consigo mismo."""
    d = df.sort_values("fecha", kind="stable").copy()
    nuevo_par = ~d.duplicated(_claves(d, ["huella_dispositivo", "id_cliente"], alcance))
    d["_nuevo_par"] = nuevo_par.astype(int)
    d["huella_clientes_distintos"] = d.groupby(_claves(d, ["huella_dispositivo"], alcance))["_nuevo_par"].cumsum()   # hasta esta fila
    d["huella_compras_previas"] = d.groupby(_claves(d, ["huella_dispositivo"], alcance)).cumcount()
    d["es_emulador"] = d["es_emulador"].astype(int)
    d["so_distinto_del_dispositivo"] = (d["sistema_operativo"] != d["dispositivo"].map(SO_DE_DISPOSITIVO)).astype(int)
    d["navegador_headless"] = d["navegador"].astype(str).str.contains("headless", case=False).astype(int)
    d["zona_horaria_no_colombia"] = (d["zona_horaria_dispositivo"] != "America/Bogota").astype(int)
    return d.drop(columns="_nuevo_par").loc[df.index]


def agregar_geo_ip(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ip_sin_ubicacion"] = (d["ip_pais"] == "Desconocido").astype(int)
    d["ip_fuera_de_colombia"] = ((d["ip_pais"] != "Colombia") & (d["ip_sin_ubicacion"] == 0)).astype(int)
    d["distancia_ip_ciudad_km"] = d["distancia_ip_ciudad_km"].fillna(0)            # con la marca ip_sin_ubicacion
    d["ip_es_datacenter"] = d["ip_es_datacenter"].astype(int)
    pares = d[["ip_pais", "zona_horaria_dispositivo"]].drop_duplicates().copy()
    pares["zona_horaria_no_coincide_ip"] = [
        int(p in TZ_ESPERADAS and z not in TZ_ESPERADAS[p]) for p, z in zip(pares["ip_pais"], pares["zona_horaria_dispositivo"])]
    return d.merge(pares, on=["ip_pais", "zona_horaria_dispositivo"], how="left").set_index(d.index)


def agregar_identidad(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["cuenta_nueva"] = (d["edad_cuenta_dias"] <= 7).astype(int)
    d["cambio_credenciales_7d"] = d["cambio_credenciales_7d"].astype(int)
    return d


def agregar_comportamiento(df: pd.DataFrame) -> pd.DataFrame:
    """El comportamiento solo se mide en web y app (el mouse, solo en web): lo que no se mide se
    rellena con un valor neutro y se avisa con una marca."""
    d = df.copy()
    d["sin_medicion_comportamiento"] = d["tiempo_formulario_seg"].isna().astype(int)
    d["sin_medicion_mouse"] = d["mouse_natural"].isna().astype(int)
    for columna, neutro in config.NEUTRO_COMPORTAMIENTO.items():
        d[columna] = d[columna].fillna(neutro)
    return d


FUNCIONES = {"hora": agregar_hora, "historial": agregar_historial, "perfil": agregar_perfil,
             "huella": agregar_huella, "geo_ip": agregar_geo_ip, "identidad": agregar_identidad,
             "comportamiento": agregar_comportamiento}


def registrar_conjunto(nombre: str, grupos: Sequence[str]) -> None:
    """Agrega un conjunto de variables nuevo (por ejemplo, el completo sin un grupo, para medir su aporte)."""
    grupos = list(grupos)
    desconocidos = [g for g in grupos if g not in config.GRUPOS_VARIABLES]
    if desconocidos:
        raise ValueError(f"Grupos desconocidos: {desconocidos}. Opciones: {list(config.GRUPOS_VARIABLES)}")
    if "perfil" in grupos and "historial" not in grupos:
        raise ValueError("El grupo 'perfil' necesita el grupo 'historial'.")
    config.CONJUNTOS_GRUPOS[nombre] = grupos


def columnas_de(conjunto: str) -> Tuple[List[str], List[str]]:
    """Devuelve (numéricas, categóricas) del conjunto de variables elegido."""
    if conjunto not in config.CONJUNTOS_GRUPOS:
        raise ValueError(f"Conjunto desconocido '{conjunto}'. Opciones: {list(config.CONJUNTOS_GRUPOS)}")
    numericas = list(config.NUMERICAS_BASE)
    categoricas = list(config.CATEGORICAS)
    for grupo in config.CONJUNTOS_GRUPOS[conjunto]:
        num, cat = config.GRUPOS_VARIABLES[grupo]
        numericas += num
        categoricas += cat
    prohibidas = set(numericas + categoricas) & set(config.COLUMNAS_PROHIBIDAS)
    if prohibidas:
        raise ValueError(f"Estas columnas no pueden ser variables de entrada: {sorted(prohibidas)}")
    return numericas, categoricas


def derivar_estado_dispositivo(df: pd.DataFrame, alcance: Optional[str] = None, primer_pago: str = "Nuevo") -> pd.Series:
    """Estado del dispositivo deducido del historial (para cuando el comercio no lo manda).

    - «Conocido»: este cliente ya había pagado con esta misma huella (en este comercio, si el alcance es 'comercio').
    - «Nuevo»:    el cliente ya tenía compras, pero nunca con esta huella.
    - primer pago del cliente: `primer_pago` (por defecto «Nuevo»: no hay forma de que el equipo ya sea conocido).
    Nunca devuelve «No reconocido»: eso solo lo puede saber el sistema de cuentas del comercio. Usa solo compras anteriores.
    """
    d = df.sort_values("fecha", kind="stable")
    antes_cliente = d.groupby(_claves(d, ["id_cliente"], alcance)).cumcount()                       # compras previas del cliente
    antes_con_huella = d.groupby(_claves(d, ["id_cliente", "huella_dispositivo"], alcance)).cumcount()   # previas con esta misma huella
    estado = pd.Series("Nuevo", index=d.index)
    estado[antes_con_huella > 0] = "Conocido"
    estado[antes_cliente == 0] = primer_pago
    return estado.loc[df.index]


CON_ALCANCE = ("historial", "perfil", "huella")        # los grupos que dependen de a quién se le mira el pasado


def construir(df: pd.DataFrame, conjunto: str = config.CONJUNTO_POR_DEFECTO, alcance: Optional[str] = None) -> pd.DataFrame:
    """Agrega todo lo que necesita el conjunto elegido (falla con un mensaje claro si faltan columnas).

    `alcance`: 'plataforma' (historial con todos los comercios juntos) o 'comercio' (cada comercio, el suyo). Por defecto, config.ALCANCE_HISTORIAL."""
    columnas_de(conjunto)                       # valida el nombre y que no haya columnas prohibidas
    d = df
    for grupo in config.CONJUNTOS_GRUPOS[conjunto]:
        faltan = [c for c in REQUIERE[grupo] if c not in d.columns]
        if faltan:
            raise ValueError(f"El conjunto '{conjunto}' necesita columnas que este archivo no trae: {faltan}")
        d = FUNCIONES[grupo](d, alcance) if grupo in CON_ALCANCE else FUNCIONES[grupo](d)
    return d
