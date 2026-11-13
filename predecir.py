"""Usar un modelo guardado para decidir sobre un pago.

Uso:  python -m modelo_ia.predecir --ejemplo
"""
import argparse
from pathlib import Path
from typing import Dict, Iterable, Optional

import joblib
import pandas as pd

from . import config, explicacion, modelos
from .reglas import SEVERIDAD, MotorReglas
from .reglas.seguridad import construir_reglas_seguridad


def cargar_artefacto(ruta: Optional[Path] = None) -> Dict:
    """Carga 'modelo_actual.joblib' o, si no existe, el modelo guardado más reciente."""
    if ruta is None:
        actual = config.CARPETA_MODELOS / "modelo_actual.joblib"
        if actual.exists():
            ruta = actual
        else:
            guardados = sorted(config.CARPETA_MODELOS.glob("*.joblib"), key=lambda p: p.stat().st_mtime)
            if not guardados:
                raise FileNotFoundError("No hay modelos guardados. Corre primero: python -m modelo_ia.entrenar")
            ruta = guardados[-1]
    return joblib.load(ruta)


def cargar_artefacto_sin_estado() -> Optional[Dict]:
    """El modelo para pagos donde el comercio no manda el estado del dispositivo (None si no existe)."""
    ruta = config.CARPETA_MODELOS / "modelo_actual_sin_estado.joblib"
    return joblib.load(ruta) if ruta.exists() else None


def analizar_transaccion(pago: Dict, artefacto: Dict, usar_reglas_seguridad: bool = True,
                         lista_negra: Iterable[str] = ()) -> Dict:
    """Recibe un pago (con las variables del modelo) y devuelve probabilidad, decisión y motivos.

    Si el modelo usa historial del cliente, el pago debe traer esas variables ya calculadas
    (la tienda o el servicio de puntuación las calcula con las compras anteriores).

    La decisión final es la más severa entre la del modelo y la de la red de seguridad
    (reglas duras de política). Si una regla necesita un campo que el pago no trae, no se aplica.
    """
    meta = artefacto["meta"]
    columnas = meta["numericas"] + meta["categoricas"]
    faltan = [c for c in columnas if c not in pago]
    if faltan:
        raise ValueError(f"Al pago le faltan campos: {faltan}")
    fila = pd.DataFrame([pago])[columnas]
    pipeline = artefacto["pipeline"]
    probabilidad = float(modelos.puntajes(pipeline, fila)[0])
    if probabilidad >= meta["umbral_bloquear"]:
        decision_modelo = "BLOQUEAR"
    elif probabilidad >= meta["umbral_revisar"]:
        decision_modelo = "REVISAR"
    else:
        decision_modelo = "APROBAR"

    decision, reglas_activadas = decision_modelo, []
    if usar_reglas_seguridad:
        motor = MotorReglas(construir_reglas_seguridad(lista_negra))
        resultado = motor.explicar(pago)
        reglas_activadas = resultado["reglas_activadas"]
        if SEVERIDAD[resultado["decision"]] > SEVERIDAD[decision_modelo]:
            decision = resultado["decision"]
    return {"probabilidad_fraude": round(probabilidad, 3), "decision": decision,
            "decision_modelo": decision_modelo, "reglas_activadas": reglas_activadas,
            "motivos": explicacion.explicar(pipeline, fila)}


EJEMPLO_NORMAL = {
    "monto_cop": 180_000, "transacciones_ultimas_24h": 1, "hora": 15, "es_madrugada": 0,
    "es_fin_semana": 0, "minutos_desde_anterior": 9000, "ciudad_distinta_anterior": 0,
    "n_compras_previas": 12, "monto_vs_promedio": 0.8, "dispositivo_nuevo_cliente": 0,
    "dispositivo": "iPhone", "localizacion": "Bogotá, Colombia", "metodo_pago": "Nequi",
    "canal_pago": "App móvil", "tipo_ip": "Móvil", "estado_dispositivo": "Conocido",
    "categoria": "Ropa",
    # variables nuevas (modelos con el conjunto "completo"); los modelos antiguos las ignoran
    "ip_frecuencia_previa": 0.9, "franja_frecuencia_previa": 0.8, "huella_clientes_distintos": 1,
    "huella_compras_previas": 11, "es_emulador": 0, "so_distinto_del_dispositivo": 0, "navegador_headless": 0,
    "zona_horaria_no_colombia": 0, "sistema_operativo": "iOS", "navegador": "App nativa", "resolucion_pantalla": "390x844",
    "ip_fuera_de_colombia": 0, "ip_sin_ubicacion": 0, "distancia_ip_ciudad_km": 6, "ip_es_datacenter": 0,
    "ip_reputacion": 90, "zona_horaria_no_coincide_ip": 0, "ip_pais": "Colombia", "edad_cuenta_dias": 700,
    "cuenta_nueva": 0, "intentos_fallidos_24h": 0, "cambio_credenciales_7d": 0, "kyc_nivel": "Completo",
    "autenticacion_2fa": "No exigida", "tiempo_formulario_seg": 40.0, "velocidad_tecleo_ms": 170.0,
    "pego_numero_tarjeta": 0.0, "mouse_natural": 0.8, "sin_medicion_comportamiento": 0, "sin_medicion_mouse": 1}
EJEMPLO_VIAJE_IMPOSIBLE = {
    "monto_cop": 2_400_000, "transacciones_ultimas_24h": 2, "hora": 16, "es_madrugada": 0,
    "es_fin_semana": 0, "minutos_desde_anterior": 12, "ciudad_distinta_anterior": 1,
    "n_compras_previas": 12, "monto_vs_promedio": 6.0, "dispositivo_nuevo_cliente": 1,
    "dispositivo": "Android", "localizacion": "Cali, Colombia", "metodo_pago": "Visa",
    "canal_pago": "Web", "tipo_ip": "Residencial", "estado_dispositivo": "Nuevo",
    "categoria": "Electrónica",
    "ip_frecuencia_previa": 0.1, "franja_frecuencia_previa": 0.3, "huella_clientes_distintos": 1,
    "huella_compras_previas": 0, "es_emulador": 0, "so_distinto_del_dispositivo": 0, "navegador_headless": 0,
    "zona_horaria_no_colombia": 0, "sistema_operativo": "Android", "navegador": "Chrome", "resolucion_pantalla": "412x915",
    "ip_fuera_de_colombia": 0, "ip_sin_ubicacion": 0, "distancia_ip_ciudad_km": 320, "ip_es_datacenter": 0,
    "ip_reputacion": 55, "zona_horaria_no_coincide_ip": 0, "ip_pais": "Colombia", "edad_cuenta_dias": 700,
    "cuenta_nueva": 0, "intentos_fallidos_24h": 2, "cambio_credenciales_7d": 1, "kyc_nivel": "Completo",
    "autenticacion_2fa": "Exigida-fallida", "tiempo_formulario_seg": 11.0, "velocidad_tecleo_ms": 80.0,
    "pego_numero_tarjeta": 1.0, "mouse_natural": 0.2, "sin_medicion_comportamiento": 0, "sin_medicion_mouse": 0}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Decide sobre un pago de ejemplo con el modelo guardado.")
    ap.add_argument("--modelo", type=Path, default=None, help="archivo .joblib (por defecto, el actual)")
    ap.add_argument("--ejemplo", action="store_true", help="probar con dos pagos de ejemplo")
    a = ap.parse_args(argv)
    artefacto = cargar_artefacto(a.modelo)
    meta = artefacto["meta"]
    print(f"Modelo: {meta['modelo']} v{meta['version']} (variables: {meta['conjunto']})")
    if a.ejemplo:
        for nombre, pago in [("Pago normal", EJEMPLO_NORMAL), ("Viaje imposible", EJEMPLO_VIAJE_IMPOSIBLE)]:
            print(nombre, "->", analizar_transaccion(pago, artefacto))


if __name__ == "__main__":
    main()
