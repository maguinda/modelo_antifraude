"""Elige, de los modelos que ya existen, el más completo que el archivo del comercio puede alimentar.

Un modelo con más grupos de variables detecta más, pero exige más datos. Si faltan datos que el modelo necesita, el servicio queda en «modo degradado»
y manda todo a revisión: un diagnóstico inútil. Por eso se prueba, de más completo a menos, cuál tiene TODO lo que pide.
"""
from pathlib import Path
from typing import Dict, List, Optional

import joblib

from .. import config, variables

# Campo interno del modelo -> maneras de obtenerlo desde lo que trae el archivo (cualquiera de las listas basta; una lista = todas sus rutas juntas).
FUENTES: Dict[str, List[List[str]]] = {
    "fecha": [["fecha_pago"]], "hora": [["fecha_pago"]], "id_cliente": [["id_cliente"]], "monto_cop": [["monto_cop"]], "localizacion": [["ciudad"]],
    "dispositivo": [["dispositivo.tipo"]], "tipo_ip": [["red.ip"], ["red.tipo_ip"]],
    "huella_dispositivo": [["dispositivo.huella"]], "sistema_operativo": [["dispositivo.sistema_operativo"]], "navegador": [["dispositivo.navegador"]],
    "resolucion_pantalla": [["dispositivo.resolucion_pantalla"]], "zona_horaria_dispositivo": [["dispositivo.zona_horaria"]], "es_emulador": [["dispositivo.es_emulador"]],
    "ip_pais": [["red.ip"], ["red.pais"]], "ip_es_datacenter": [["red.ip"], ["red.es_datacenter"]], "ip_reputacion": [["red.ip"], ["red.reputacion"]],
    "distancia_ip_ciudad_km": [["red.ip"], ["red.distancia_a_ciudad_km"]],
    "kyc_nivel": [["identidad.kyc_nivel"]], "edad_cuenta_dias": [["identidad.edad_cuenta_dias"]], "autenticacion_2fa": [["identidad.autenticacion_2fa"]],
    "intentos_fallidos_24h": [["identidad.intentos_fallidos_24h"]], "cambio_credenciales_7d": [["identidad.cambio_credenciales_7d"]],
    "tiempo_formulario_seg": [["comportamiento.tiempo_formulario_seg"]], "velocidad_tecleo_ms": [["comportamiento.velocidad_tecleo_ms"]],
    "pego_numero_tarjeta": [["comportamiento.pego_numero_tarjeta"]], "mouse_natural": [["comportamiento.mouse_natural"]],
}
OPCIONALES = {"tiempo_formulario_seg", "velocidad_tecleo_ms", "pego_numero_tarjeta", "mouse_natural", "distancia_ip_ciudad_km"}     # el servicio acepta que falten
MIN_COBERTURA = 0.95


def candidatos() -> List[Dict]:
    """Los modelos guardados de la versión actual, del más completo al menos."""
    salida = []
    for ruta in sorted(Path(config.CARPETA_MODELOS).glob(f"gradient_boosting_*_v{config.VERSION}.joblib")):
        art = joblib.load(ruta)
        meta = art["meta"]
        salida.append({"ruta": ruta, "artefacto": art, "conjunto": meta["conjunto"], "sin_estado": bool(meta.get("sin_estado")),
                       "grupos": list(config.CONJUNTOS_GRUPOS.get(meta["conjunto"], []))})
    return sorted(salida, key=lambda c: (-len(c["grupos"]), c["sin_estado"]))


def _disponible(campo: str, cobertura: Dict[str, float]) -> bool:
    return any(all(cobertura.get(r, 0.0) >= MIN_COBERTURA for r in ruta) for ruta in FUENTES.get(campo, []))


def faltantes(candidato: Dict, cobertura: Dict[str, float]) -> List[str]:
    """Campos internos que el modelo necesita y el archivo no trae (con suficiente cobertura)."""
    falta = []
    for g in candidato["grupos"]:
        for c in variables.REQUIERE[g]:
            if c not in OPCIONALES and not _disponible(c, cobertura) and c not in falta:
                falta.append(c)
    if not _disponible("tipo_ip", cobertura) and "tipo_ip" not in falta:
        falta.append("tipo_ip")
    if not candidato["sin_estado"] and not any(cobertura.get(r, 0.0) >= MIN_COBERTURA for r in ("dispositivo.estado",)):
        falta.append("estado_dispositivo")
    return falta


def elegir(cobertura: Dict[str, float], forzar: Optional[str] = None) -> Dict:
    """Devuelve {'candidato', 'no_usados': {grupo: [campos que faltan]}, 'descartados': [...]} del mejor modelo posible."""
    cands = candidatos()
    if forzar and forzar != "auto":
        cands = [c for c in cands if c["conjunto"] == forzar]
        if not cands:
            raise ValueError(f"No hay un modelo guardado para el nivel «{forzar}».")
    descartados = []
    for c in cands:
        f = faltantes(c, cobertura)
        if not f or (forzar and forzar != "auto"):
            elegido = c
            todos = [g for g in config.CONJUNTOS_GRUPOS["completo"] if g not in c["grupos"]]
            no_usados = {}
            for g in todos:
                faltan = [x for x in variables.REQUIERE[g] if x not in OPCIONALES and not _disponible(x, cobertura)]
                no_usados[g] = faltan
            return {"candidato": elegido, "no_usados": no_usados, "descartados": descartados, "faltan_del_elegido": f}
        descartados.append({"modelo": f"{c['conjunto']}{' (sin estado)' if c['sin_estado'] else ''}", "faltan": f})
    raise ValueError("Ningún modelo se puede alimentar con este archivo. Falta lo mínimo: " + ", ".join(faltantes(cands[-1], cobertura)))
