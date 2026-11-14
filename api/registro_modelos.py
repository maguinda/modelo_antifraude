"""Verificar la huella de un modelo ANTES de cargarlo, registrarlo y adoptar los que ya se usaban.

Por qué: un `.joblib` se carga con `joblib.load`, que es una deserialización de Python y EJECUTA código. Si alguien cambiara el archivo (por error o a propósito),
ese código correría al arrancar la API. Aquí el archivo se lee UNA vez, se calcula su SHA-256 sobre esos mismos bytes, se compara con la huella del registro
y solo si coincide se deserializa desde esos bytes (así no hay una ventana entre «verificar» y «cargar» en la que cambiarlo).

Si hay un modelo EN USO en el registro y su huella no coincide, el servicio NO arranca. Si no hay registro todavía, se mantiene el comportamiento de antes
(modelo_actual.joblib) con un aviso en el registro de eventos, salvo que se exija el registro (`MODELO_IA_EXIGIR_REGISTRO=1`).

Límite: la huella protege de cambios accidentales o de un archivo sustituido; no protege si alguien controla el servidor entero (podría cambiar también la base).
"""

import hashlib
import hmac
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib

from .. import config, deriva, predecir

log = logging.getLogger("modelo_ia.modelos")
ROLES = ("principal", "sin_estado")
ACTUALES = {
    "principal": "modelo_actual.joblib",
    "sin_estado": "modelo_actual_sin_estado.joblib",
}  # los nombres de siempre (copias del versionado)


class ModeloAlterado(RuntimeError):
    """El archivo del modelo no es el que se registró (huella distinta) o ya no existe: el servicio no debe usarlo."""


class RegistroIncompleto(RuntimeError):
    """Se exige el registro (MODELO_IA_EXIGIR_REGISTRO) y no hay un modelo en uso registrado."""


def sha256_de_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def ruta_del_modelo(archivo: str, carpeta: Optional[Path] = None) -> Path:
    """La ruta de un modelo registrado. El registro guarda SOLO el nombre: nunca una ruta (no se puede salir de la carpeta de modelos)."""
    if not archivo or archivo != Path(archivo).name or not archivo.endswith(".joblib") or archivo.startswith("."):
        raise ValueError("Nombre de archivo de modelo no válido.")
    return (carpeta or config.CARPETA_MODELOS) / archivo


def leer_verificado(ruta: Path, huella_esperada: str) -> bytes:
    """Los bytes del archivo, solo si su SHA-256 es el esperado."""
    try:
        datos = ruta.read_bytes()
    except OSError:
        raise ModeloAlterado(f"No se pudo leer el archivo del modelo registrado ({ruta.name}).")
    real = sha256_de_bytes(datos)
    if not hmac.compare_digest(real, huella_esperada):
        raise ModeloAlterado(
            f"El archivo del modelo {ruta.name} NO coincide con el que se registró (huella {real[:12]}… en lugar de {huella_esperada[:12]}…). "
            "No se carga. Si lo cambiaste a propósito, regístralo y promuévelo (admin modelo-registrar / modelo-promover); si no, restaura el archivo."
        )
    return datos


def leer_meta(ruta_joblib: Path) -> Dict[str, Any]:
    """Los datos de trazabilidad que `entrenar` guarda junto al modelo (.json). Es JSON puro: leerlo no ejecuta nada."""
    ruta = ruta_joblib.with_suffix(".json")
    try:
        meta = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"No se pudo leer {ruta.name} (los datos de trazabilidad que acompañan al modelo).")
    faltan = [k for k in ("modelo", "version", "conjunto") if not isinstance(meta.get(k), str) or not meta[k]]
    if faltan:
        raise ValueError(f"A {ruta.name} le faltan datos: {', '.join(faltan)}.")
    return meta


def registrar_archivo(almacen: Any, ruta: Path, rol: Optional[str] = None) -> int:
    """Registra un modelo de la carpeta de modelos como CANDIDATO. El rol se deduce de sus datos (sin_estado → «sin_estado») y, si se indica, debe coincidir."""
    ruta = Path(ruta)
    if ruta.resolve().parent != config.CARPETA_MODELOS.resolve():
        raise ValueError(f"El modelo debe estar en la carpeta de modelos ({config.CARPETA_MODELOS.name}/).")
    ruta_del_modelo(ruta.name)  # nombre válido
    if not ruta.exists():
        raise ValueError("No existe ese archivo de modelo.")
    meta = leer_meta(ruta)
    deducido = "sin_estado" if meta.get("sin_estado") else "principal"
    if rol is not None and rol != deducido:
        raise ValueError(
            f"Ese modelo es «{deducido}» (según sus datos), no «{rol}»: un modelo sin estado del dispositivo solo sirve como «sin_estado» y al revés."
        )
    return int(
        almacen.registro_modelos.registrar(
            ruta.name,
            sha256_de(ruta),
            meta["modelo"],
            meta["version"],
            meta["conjunto"],
            deducido,
            meta.get("referencia_puntaje"),
        )
    )


def adoptar_actuales(almacen: Any) -> List[Tuple[str, int, str]]:
    """Registra y pone EN USO los modelos que el servicio ya usaba (modelo_actual*.joblib): se busca, entre los modelos versionados de la carpeta, el que es
    idéntico (misma huella) y se registra ese, con sus datos. Devuelve [(rol, número, archivo)]. Si un «actual» no coincide con ninguno, se explica y no se hace nada."""
    carpeta = config.CARPETA_MODELOS
    adoptados: List[Tuple[str, int, str]] = []
    versionados = sorted(p for p in carpeta.glob("*.joblib") if p.name not in ACTUALES.values())
    for rol, nombre in ACTUALES.items():
        actual = carpeta / nombre
        if not actual.exists():
            continue
        huella = sha256_de(actual)
        gemelo = next((p for p in versionados if sha256_de(p) == huella), None)
        if gemelo is None:
            raise ValueError(
                f"{nombre} no es idéntico a ningún modelo versionado de la carpeta: no se sabe de qué versión viene. Registra el modelo versionado a mano."
            )
        existente = next(
            (m for m in almacen.registro_modelos.listar() if m["rol"] == rol and m["sha256"] == huella), None
        )
        id_ = int(existente["id"]) if existente else registrar_archivo(almacen, gemelo, rol)
        if not existente or existente["estado"] != "en_uso":
            almacen.registro_modelos.promover(id_)
        adoptados.append((rol, id_, gemelo.name))
    if not adoptados:
        raise ValueError("No hay modelo_actual*.joblib que adoptar.")
    return adoptados


def _cargar_de_registro(almacen: Any, rol: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    fila = almacen.registro_modelos.en_uso(rol)
    if fila is None:
        return None
    datos = leer_verificado(ruta_del_modelo(fila["archivo"]), fila["sha256"])
    return joblib.load(io.BytesIO(datos)), {
        "id": fila["id"],
        "huella": fila["sha256"][:12],
        "version": fila["version"],
        "verificado": True,
    }


def preparar(
    almacen: Any, ajustes: Any, artefacto: Optional[Dict[str, Any]], artefacto_sin_estado: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any]]:
    """Lo que usa `crear_app`: un modelo que ya viene dado (pruebas, uso como biblioteca) se respeta tal cual; si no, se carga del registro con verificación."""
    if artefacto is not None:
        return (
            artefacto,
            artefacto_sin_estado,
            {"principal": {"id": None, "huella": None, "version": None, "verificado": False}},
        )
    cargado, sin_estado_cargado, info = cargar_para_servicio(almacen, ajustes)
    return cargado, artefacto_sin_estado if artefacto_sin_estado is not None else sin_estado_cargado, info


def cargar_para_servicio(almacen: Any, ajustes: Any) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any]]:
    """(modelo principal, modelo sin estado o None, qué se verificó). Falla (ModeloAlterado, RegistroIncompleto) en vez de usar un modelo que no se pudo verificar
    cuando hay registro; sin registro y sin exigirlo, usa el de siempre con un aviso."""
    exigir = bool(getattr(ajustes, "exigir_registro_modelos", False))
    sin_verificar = {"id": None, "huella": None, "version": None, "verificado": False}
    if (
        getattr(ajustes, "ruta_modelo", None) is not None
    ):  # un modelo elegido a mano (MODELO_IA_MODELO): no pasa por el registro
        if exigir:
            raise RegistroIncompleto(
                "MODELO_IA_MODELO elige un modelo a mano y se salta el registro, pero MODELO_IA_EXIGIR_REGISTRO está activo."
            )
        log.warning("Se usa el modelo indicado en MODELO_IA_MODELO: NO se verifica contra el registro.")
        return (
            predecir.cargar_artefacto(ajustes.ruta_modelo),
            predecir.cargar_artefacto_sin_estado(),
            {"principal": sin_verificar, "sin_estado": sin_verificar},
        )
    info: Dict[str, Any] = {}
    principal = _cargar_de_registro(almacen, "principal")
    if principal is not None:
        artefacto, info["principal"] = principal
    elif exigir:
        raise RegistroIncompleto(
            "No hay un modelo EN USO en el registro (MODELO_IA_EXIGIR_REGISTRO está activo). Usa «admin modelo-adoptar» o «modelo-registrar» y «modelo-promover»."
        )
    else:
        log.warning(
            "No hay un modelo en el registro: se usa modelo_actual.joblib SIN verificar su huella. Usa «admin modelo-adoptar»."
        )
        artefacto, info["principal"] = predecir.cargar_artefacto(None), sin_verificar
    sin_estado = _cargar_de_registro(almacen, "sin_estado")
    if sin_estado is not None:
        artefacto_sin_estado, info["sin_estado"] = sin_estado
    elif exigir:
        artefacto_sin_estado, info["sin_estado"] = (
            None,
            sin_verificar,
        )  # con registro exigido no se carga uno sin verificar
    else:
        artefacto_sin_estado, info["sin_estado"] = predecir.cargar_artefacto_sin_estado(), sin_verificar
    return artefacto, artefacto_sin_estado, info


def verificar_en_uso(almacen: Any) -> List[Tuple[str, str, str]]:
    """Comprueba la huella de los modelos EN USO sin cargarlos. Devuelve [(rol, archivo, «ok» o el problema)]."""
    resultado = []
    for rol in ROLES:
        fila = almacen.registro_modelos.en_uso(rol)
        if fila is None:
            continue
        try:
            leer_verificado(ruta_del_modelo(fila["archivo"]), fila["sha256"])
            resultado.append((rol, fila["archivo"], "ok"))
        except ModeloAlterado as e:
            resultado.append((rol, fila["archivo"], str(e)))
    return resultado


def calcular_referencia(almacen: Any, id_modelo: int, ruta_datos: Optional[Path] = None) -> Dict[str, Any]:
    """Calcula y guarda la distribución de referencia de los puntajes de un modelo YA ENTRENADO: se puntúa su conjunto de validación (el mismo corte por fecha
    que usó `entrenar`) con el modelo, que antes se verifica contra su huella. Sirve para los modelos que se entrenaron antes de que `entrenar` la guardara."""
    from .. import entrenar, modelos

    fila = almacen.registro_modelos.por_id(id_modelo)
    if fila is None:
        raise ValueError("No existe un modelo registrado con ese número.")
    artefacto = joblib.load(io.BytesIO(leer_verificado(ruta_del_modelo(fila["archivo"]), fila["sha256"])))
    meta = artefacto["meta"]
    df, mascaras = entrenar.preparar_datos(ruta_datos, meta["conjunto"], meta.get("alcance_historial"))
    if meta.get("sin_estado"):
        df = df.assign(estado_dispositivo=entrenar.ESTADO_RELLENO)
    validacion = df.loc[mascaras["validacion"], meta["numericas"] + meta["categoricas"]]
    if len(validacion) == 0:
        raise ValueError("El conjunto de validación de esos datos está vacío: no hay con qué calcular la referencia.")
    puntajes = modelos.puntajes(artefacto["pipeline"], validacion)
    referencia = {"n": int(len(puntajes)), "proporciones": deriva.proporciones(deriva.histograma(puntajes.tolist()))}
    almacen.registro_modelos.guardar_referencia(id_modelo, referencia)
    return referencia
