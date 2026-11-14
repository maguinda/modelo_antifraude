"""Las señales que más pesaron en el puntaje de UN pago, en palabras que entienda quien revisa.

Cómo funciona (sirve para cualquier modelo, también Gradient Boosting, que no explica nada por sí solo):
  1. Se agrupan las variables del modelo en «señales» que una persona entiende (el monto, el dispositivo, la conexión, la cuenta...).
  2. Se parte de un pago TÍPICO (la mediana de los pagos legítimos con los que se entrenó el modelo) y se van «poniendo» las señales reales de este
     pago, una por una, en muchos órdenes al azar. Lo que sube el puntaje en cada paso es lo que aporta esa señal (valores de Shapley estimados).
     Así las señales que se refuerzan entre sí se reparten el mérito y los puntos SUMAN exactamente (puntaje del pago - puntaje del pago típico).
  3. Se muestran las que más pesaron, con los puntos de probabilidad que aportaron (positivo: empujó hacia fraude; negativo: a favor).
  Las frases dicen HECHOS («la cuenta tiene 20 días»), no juicios («cuenta antigua»), porque el modelo puede considerar de más o menos riesgo algo
  que a una persona le parece normal.

Qué NO es: no prueba que haya fraude ni explica por qué alguien lo cometería. Son las señales que más pesaron EN EL MODELO. Con otro «pago
típico» de referencia saldrían números algo distintos, y los pesos son una estimación (con 12 órdenes al azar el ruido es de pocos puntos).
Se calcula solo para pagos que no se aprueban (alrededor del 13 %) y nunca puede romper una decisión: si algo falla, no se muestran señales.

Para modelos ya entrenados que no traen su referencia:  python -m modelo_ia.explicacion_local agregar-referencia
"""
import argparse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

from . import config, modelos

MINIMO_PUNTOS = 2.0          # cambios menores a esto (en puntos de probabilidad) no se muestran: son ruido
TOPE_SUBEN, TOPE_BAJAN = 4, 2
ORDENES = 12                 # órdenes al azar para repartir el mérito entre señales (con sus inversos); más órdenes = menos ruido y más tiempo
CORTE_MUESTRA = 5000


def _cop(x) -> str:
    return "$ " + f"{float(x):,.0f}".replace(",", ".")


# señal -> (variables que la forman, frase(fila, sube)). La frase devuelve None si, para esa dirección, no hay nada concreto que decir.
def _num(f, k, defecto=0.0) -> float:
    try:
        v = f.get(k, defecto)
        return float(defecto if v is None else v)
    except (TypeError, ValueError):
        return float(defecto)


def _monto(f, sube, ref):
    r, n = _num(f, "monto_vs_promedio"), _num(f, "n_compras_previas")
    tipico = _num(ref, "monto_cop")
    veces = _num(f, "monto_cop") / tipico if tipico else 0
    m = _cop(f["monto_cop"])
    if sube:
        if n > 0 and r >= 1.5:
            return f"El monto ({m}) es {str(round(r, 1)).replace('.', ',')} veces el promedio de este cliente"
        return f"El monto ({m}) es unas {veces:.0f} veces el monto típico" if veces >= 2 else f"El monto ({m}) pesa en el puntaje"
    return f"El monto ({m}) no es alto" + (" para este cliente" if n > 0 else "") if veces < 2 and (n == 0 or r < 1.5) else None


def _primera(f, sube, ref):
    n = int(_num(f, "n_compras_previas"))
    if sube:
        return "Es la primera compra de este cliente en la tienda" if n == 0 else f"El cliente tiene pocas compras previas ({n})"
    return f"El cliente tiene {n} compras previas" if n >= 3 else None


def _ritmo(f, sube, ref):
    n24, m = _num(f, "transacciones_ultimas_24h", 1), _num(f, "minutos_desde_anterior")
    if sube:
        if n24 >= 2 or 0 < m < 60:
            return f"Varios pagos seguidos: {int(n24)} en las últimas 24 h" + (f", el anterior hace {m:.0f} min" if 0 < m < 1440 else "")
        return "El ritmo de compras pesa en el puntaje"
    return "El ritmo de compras es normal" if n24 <= 2 and (m == 0 or m >= 60) else None


def _ciudad(f, sube, ref):
    distinta = _num(f, "ciudad_distinta_anterior") > 0
    if sube:
        return f"La ciudad ({f.get('localizacion', '—')}) es distinta a la de su compra anterior" if distinta else f"La ciudad ({f.get('localizacion', '—')}) pesa en el puntaje"
    return "La ciudad es la misma de su compra anterior" if not distinta and _num(f, "n_compras_previas") > 0 else None


def _dispositivo(f, sube, ref):
    e, nuevo = str(f.get("estado_dispositivo", "")), _num(f, "dispositivo_nuevo_cliente") > 0
    if sube:
        if e == "No reconocido":
            return "El dispositivo no es reconocido por la tienda"
        return "Pagó desde un dispositivo que este cliente no había usado" if nuevo or e == "Nuevo" else "El dispositivo pesa en el puntaje"
    return "Pagó desde un dispositivo que el cliente ya usaba" if not nuevo and e not in ("Nuevo", "No reconocido") and _num(f, "n_compras_previas") > 0 else None


def _hora(f, sube, ref):
    h = int(_num(f, "hora", 12))
    if sube:
        return f"Pago de madrugada ({h}:00)" if _num(f, "es_madrugada") > 0 else f"La hora del pago ({h}:00) pesa en el puntaje"
    return "La hora del pago es de las habituales" if 7 <= h <= 22 else None


def _ip_habitual(f, sube, ref):
    previa = _num(f, "ip_frecuencia_previa")
    if sube:
        return "Es una conexión que el cliente no había usado" if previa == 0 else "La conexión pesa en el puntaje"
    return "Es una conexión que el cliente ya usaba" if previa > 0 else None


def _red(f, sube, ref):
    t = str(f.get("tipo_ip", ""))
    if sube:
        if t.upper() in ("VPN", "PROXY", "DESCONOCIDA"):
            partes = [f"Se conectó por una conexión tipo {t}" if t.upper() != "DESCONOCIDA" else "No se pudo identificar la conexión"]
            if _num(f, "ip_es_datacenter") > 0:
                partes.append("de un centro de datos")
            if _num(f, "ip_reputacion", 100) < 60:
                partes.append(f"con mala reputación ({_num(f, 'ip_reputacion'):.0f}/100)")
            return " ".join(partes)
        if not t:
            return None
        usual = str(ref.get("tipo_ip", "")).lower()
        return f"La conexión es {t.lower()}; en los pagos legítimos lo más habitual es {usual}" if usual and usual != t.lower() else f"El tipo de conexión ({t.lower()}) pesa en el puntaje"
    return f"La conexión ({t.lower()}) es de las habituales" if t.upper() in ("RESIDENCIAL", "MOVIL", "MÓVIL", "EMPRESARIAL") and _num(f, "ip_es_datacenter") == 0 else None


def _geo(f, sube, ref):
    fuera, km, pais = _num(f, "ip_fuera_de_colombia") > 0, _num(f, "distancia_ip_ciudad_km"), f.get("ip_pais", "otro país")
    if sube:
        if fuera:
            return f"La IP está en {pais}, fuera de Colombia"
        if km > 100:
            return f"La IP queda a unos {km:.0f} km de la ciudad declarada"
        return "La zona horaria del equipo no coincide con la ubicación de la IP" if _num(f, "zona_horaria_no_coincide_ip") > 0 else "La ubicación de la IP pesa en el puntaje"
    return "La IP está en Colombia y cerca de la ciudad declarada" if not fuera and km <= 100 and _num(f, "zona_horaria_no_coincide_ip") == 0 else None


def _equipo(f, sube, ref):
    emu = _num(f, "es_emulador") > 0 or _num(f, "navegador_headless") > 0
    if sube:
        if emu:
            return "Parece un emulador o un navegador automatizado"
        n = _num(f, "huella_clientes_distintos")
        return f"El mismo equipo lo han usado {n:.0f} clientes distintos" if n >= 3 else "Las características del equipo pesan en el puntaje"
    return "El equipo no parece un emulador" if not emu and _num(f, "huella_clientes_distintos") <= 1 else None


def _cuenta(f, sube, ref):
    d = _num(f, "edad_cuenta_dias")
    if sube:
        return f"Cuenta nueva ({d:.0f} días)" if d <= 30 else f"La antigüedad de la cuenta ({d:.0f} días) pesa en el puntaje"
    return f"La cuenta tiene {d:.0f} días" if d >= 90 else None


def _verificacion(f, sube, ref):
    kyc, tfa = str(f.get("kyc_nivel", "")), str(f.get("autenticacion_2fa", ""))
    if sube:
        if tfa in ("Exigida y fallida", "Exigida fallida"):
            return "El doble factor falló"
        if tfa == "No disponible":
            return "No hay doble factor para comprobar la cuenta"
        usual = str(ref.get("kyc_nivel", "")).lower()
        if kyc.lower() in ("básico", "basico", "sin verificar") or (usual and usual != kyc.lower()):
            return f"Verificación del cliente: {kyc.lower()}" + (f" (lo más habitual en pagos legítimos: {usual})" if usual and usual != kyc.lower() else "")
        return f"La verificación del cliente ({kyc.lower()}) pesa en el puntaje"
    return "Verificación del cliente: completa" if kyc.lower() == "completo" else None


def _acceso(f, sube, ref):
    n, cambio = _num(f, "intentos_fallidos_24h"), _num(f, "cambio_credenciales_7d") > 0
    if sube:
        return f"{n:.0f} intentos fallidos de ingreso en 24 h" if n else "Cambió su contraseña o sus datos de acceso esta semana" if cambio else "Los datos de acceso pesan en el puntaje"
    return "Sin intentos fallidos ni cambios de acceso recientes" if n == 0 and not cambio else None


def _formulario(f, sube, ref):
    s = _num(f, "tiempo_formulario_seg", 45)
    if sube:
        return f"Llenó el formulario en {s:.0f} s (muy rápido)" if s < 15 else "El movimiento del mouse o el tecleo no parecen de una persona"
    return "Llenó el formulario a un ritmo de persona" if 20 <= s <= 300 else None


def _pegado(f, sube, ref):
    return "Pegó el número de tarjeta en vez de teclearlo" if sube and _num(f, "pego_numero_tarjeta") > 0 else None


def _pago(f, sube, ref):
    tipo = f"{str(f.get('categoria', '—')).lower()}, {f.get('metodo_pago', '—')}, {f.get('canal_pago', '—')}"
    return f"El tipo de compra ({tipo}) tiene más riesgo en el modelo" if sube else f"El tipo de compra ({tipo}) tiene menos riesgo en el modelo"


SENALES: Dict[str, Any] = {
    "monto": (["monto_cop", "monto_vs_promedio"], _monto), "primera_compra": (["n_compras_previas"], _primera),
    "ritmo": (["transacciones_ultimas_24h", "minutos_desde_anterior"], _ritmo), "ciudad": (["ciudad_distinta_anterior", "localizacion"], _ciudad),
    "dispositivo": (["dispositivo_nuevo_cliente", "estado_dispositivo", "huella_compras_previas"], _dispositivo),
    "hora": (["hora", "es_madrugada", "es_fin_semana", "franja_frecuencia_previa"], _hora), "ip_habitual": (["ip_frecuencia_previa"], _ip_habitual),
    "red": (["tipo_ip", "ip_es_datacenter", "ip_reputacion", "ip_sin_ubicacion"], _red),
    "geo": (["ip_pais", "ip_fuera_de_colombia", "distancia_ip_ciudad_km", "zona_horaria_no_coincide_ip", "zona_horaria_no_colombia"], _geo),
    "equipo": (["es_emulador", "navegador_headless", "so_distinto_del_dispositivo", "huella_clientes_distintos", "sistema_operativo", "navegador", "resolucion_pantalla"], _equipo),
    "cuenta": (["edad_cuenta_dias", "cuenta_nueva"], _cuenta), "verificacion": (["kyc_nivel", "autenticacion_2fa"], _verificacion),
    "acceso": (["intentos_fallidos_24h", "cambio_credenciales_7d"], _acceso),
    "formulario": (["tiempo_formulario_seg", "velocidad_tecleo_ms", "mouse_natural", "sin_medicion_comportamiento", "sin_medicion_mouse"], _formulario),
    "pegado": (["pego_numero_tarjeta"], _pegado), "pago": (["metodo_pago", "canal_pago", "categoria", "dispositivo"], _pago),
}


def calcular_referencia(df: pd.DataFrame, mascara_entrenamiento, columna_objetivo: str, numericas: List[str], categoricas: List[str]) -> Dict[str, Any]:
    """El valor típico de cada variable: la mediana (numéricas) o el valor más frecuente (categóricas) de los pagos LEGÍTIMOS del entrenamiento."""
    base = df.loc[mascara_entrenamiento & (df[columna_objetivo] == 0)]
    if len(base) > 200_000:
        base = base.sample(200_000, random_state=config.SEMILLA)
    ref: Dict[str, Any] = {}
    for c in numericas:
        v = float(base[c].median())
        ref[c] = int(v) if float(v).is_integer() else round(v, 4)
    for c in categoricas:
        ref[c] = str(base[c].mode().iloc[0])
    return ref


def contribuciones(pipeline, pago: Dict[str, Any], meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reparte la diferencia entre un pago TÍPICO y este pago entre las señales (valores de Shapley, estimados con órdenes al azar).

    Para cada orden al azar se parte del pago típico y se van «poniendo» las señales reales una por una; lo que sube el puntaje en cada paso
    es lo que aporta esa señal en ese orden. Se promedia entre órdenes (y sus inversos, que reducen el ruido). Así las señales que se refuerzan
    entre sí se reparten el mérito, y los puntos SUMAN exactamente (puntaje del pago - puntaje del pago típico)."""
    ref = meta.get("referencia")
    if not ref:
        return None
    columnas = meta["numericas"] + meta["categoricas"]
    actual = {c: pago[c] for c in columnas}
    tipico = {c: (ref[c] if c in ref else actual[c]) for c in columnas}
    activas = []                                                          # señales donde el pago NO es el típico
    for clave, (variables, _) in SENALES.items():
        presentes = [v for v in variables if v in columnas and v in ref]
        if presentes and any(str(ref[v]) != str(actual[v]) for v in presentes):
            activas.append((clave, presentes))
    if not activas:
        return None
    n = len(activas)
    azar = np.random.default_rng(config.SEMILLA)
    ordenes = []
    for _ in range(ORDENES // 2):
        o = list(azar.permutation(n))
        ordenes += [o, o[::-1]]
    filas = []
    for o in ordenes:
        f = dict(tipico)
        filas.append(dict(f))
        for k in o:
            for v in activas[k][1]:
                f[v] = actual[v]
            filas.append(dict(f))
    p = modelos.puntajes(pipeline, pd.DataFrame(filas)[columnas]).reshape(len(ordenes), n + 1)
    pesos = np.zeros(n)
    for fila_p, o in zip(p, ordenes):
        for paso, k in enumerate(o):
            pesos[k] += fila_p[paso + 1] - fila_p[paso]
    pesos = pesos / len(ordenes) * 100
    return {"pesos": {activas[k][0]: float(pesos[k]) for k in range(n)}, "puntaje": float(p[0, -1]), "tipico": float(p[0, 0])}


def explicar(pipeline, pago: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Devuelve {'suben': [...], 'bajan': [...], 'puntaje_tipico': ...}; cada señal con {'clave', 'texto', 'puntos'}. Vacío si no hay referencia o algo falla."""
    vacio: Dict[str, Any] = {"suben": [], "bajan": [], "puntaje_tipico": None}
    try:
        c = contribuciones(pipeline, pago, meta)
        if c is None:
            return vacio
        ref, salida = meta["referencia"], {"suben": [], "bajan": [], "puntaje_tipico": round(c["tipico"], 4)}
        for clave, puntos in c["pesos"].items():
            puntos = round(puntos, 1)
            if abs(puntos) < MINIMO_PUNTOS:
                continue
            texto = SENALES[clave][1](pago, puntos > 0, ref)
            if texto is None:
                continue
            salida["suben" if puntos > 0 else "bajan"].append({"clave": clave, "texto": texto, "puntos": puntos})
        salida["suben"] = sorted(salida["suben"], key=lambda s: -s["puntos"])[:TOPE_SUBEN]
        salida["bajan"] = sorted(salida["bajan"], key=lambda s: s["puntos"])[:TOPE_BAJAN]
        return salida
    except Exception:                                                     # explicar nunca puede romper una decisión
        return vacio


# ------------------------------------------------------------------ modelos ya entrenados
def agregar_referencia(ruta_datos: Optional[Path] = None, carpeta: Optional[Path] = None) -> List[str]:
    """Agrega `meta['referencia']` a los modelos guardados que no la traen. No toca el modelo: se comprueba que sus predicciones no cambian."""
    from . import entrenar
    carpeta = Path(carpeta or config.CARPETA_MODELOS)
    pendientes = [r for r in sorted(carpeta.glob("*.joblib")) if "referencia" not in joblib.load(r)["meta"]]
    if not pendientes:
        return []
    df, mascaras = entrenar.preparar_datos(ruta_datos, "completo", "comercio")
    numericas = config.NUMERICAS_BASE + [x for g in config.CONJUNTOS_GRUPOS["completo"] for x in config.GRUPOS_VARIABLES[g][0]]
    categoricas = config.CATEGORICAS + [x for g in config.CONJUNTOS_GRUPOS["completo"] for x in config.GRUPOS_VARIABLES[g][1]]
    ref = calcular_referencia(df, mascaras["entrenamiento"], config.COLUMNA_OBJETIVO, list(dict.fromkeys(numericas)), list(dict.fromkeys(categoricas)))
    muestra = df.sample(CORTE_MUESTRA, random_state=config.SEMILLA)
    hechos = []
    for ruta in pendientes:
        art = joblib.load(ruta)
        meta = art["meta"]
        cols = meta["numericas"] + meta["categoricas"]
        antes = modelos.puntajes(art["pipeline"], muestra[cols])
        meta["referencia"] = {c: ref[c] for c in cols if c in ref}
        joblib.dump(art, ruta)
        despues = modelos.puntajes(joblib.load(ruta)["pipeline"], muestra[cols])
        if not np.array_equal(antes, despues):
            raise RuntimeError(f"¡Las predicciones de {ruta.name} cambiaron! No debía pasar.")
        hechos.append(ruta.name)
    return hechos


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Señales que más pesaron en cada pago.")
    sub = ap.add_subparsers(dest="orden", required=True)
    a = sub.add_parser("agregar-referencia", help="agrega la referencia a los modelos guardados que no la traen"); a.add_argument("--datos", type=Path, default=None)
    x = ap.parse_args(argv)
    hechos = agregar_referencia(x.datos)
    print(f"Referencia agregada a {len(hechos)} modelos (sus predicciones no cambiaron):", ", ".join(hechos) if hechos else "ninguno hacía falta")


if __name__ == "__main__":
    main()
