"""Consultas de SOLO LECTURA para el panel de control: listado con filtros, detalle completo de un pago, resumen y vínculos.

Reglas que se cumplen aquí (y que las pruebas vigilan):
- Todo es lectura: ninguna consulta escribe en la base.
- Un comercio solo ve lo suyo (si pide el de otro, 404 como en el resto de la API). Analistas y administradores ven todos.
- Este archivo no tiene SQL: valida los filtros, pide las filas al repositorio (`repositorios/consultas.py`) y da forma a la respuesta.
- La IP y la huella del dispositivo NUNCA se muestran: solo códigos cortos (p. ej. `7F3A91C2`) que permiten reconocer repeticiones.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .. import deriva
from .almacen import Almacen, Principal
from .repositorios.interfaces import ORDENES_PANEL
from .servicio import ErrorAPI, ServicioEvaluacion

ORDENES = ORDENES_PANEL  # los nombres de orden; el SQL de cada uno vive en el repositorio
DECISIONES = ("APROBADA", "RECHAZADA", "EN_REVISION")
PATRON_CODIGO = re.compile(r"^[0-9A-Fa-f]{8}$")
EXCLUIDOS = {"ip_huella", "huella_dispositivo"}  # nunca se muestran: se reemplazan por códigos cortos
GRUPOS = {
    "pago": ["monto_cop", "metodo_pago", "canal_pago", "categoria", "localizacion", "fecha", "hora", "mes"],
    "dispositivo": [
        "dispositivo",
        "estado_dispositivo",
        "sistema_operativo",
        "navegador",
        "resolucion_pantalla",
        "zona_horaria_dispositivo",
        "es_emulador",
    ],
    "red": ["tipo_ip", "ip_pais", "ip_ciudad_estimada", "distancia_ip_ciudad_km", "ip_es_datacenter", "ip_reputacion"],
    "identidad": [
        "kyc_nivel",
        "edad_cuenta_dias",
        "autenticacion_2fa",
        "intentos_fallidos_24h",
        "cambio_credenciales_7d",
    ],
    "comportamiento": ["tiempo_formulario_seg", "velocidad_tecleo_ms", "pego_numero_tarjeta", "mouse_natural"],
    "calculado_por_la_api": ["transacciones_ultimas_24h"],
}
CAMPOS_FILA_DEL_PAGO = {"id_cliente", "estado_origen"}


def _fecha(valor: Optional[str], nombre: str) -> Optional[date]:
    if valor is None:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ErrorAPI(422, "Fecha no válida", f"`{nombre}` debe ser una fecha real con formato AAAA-MM-DD.")


class Filtros:
    """Los filtros pedidos, ya VALIDADOS (roles, fechas, códigos), y nada más: no tiene SQL. El repositorio los traduce a condiciones con parámetros."""

    def __init__(
        self,
        principal: Principal,
        comercio_id: Optional[int] = None,
        desde: Optional[str] = None,
        hasta: Optional[str] = None,
        decision: Optional[str] = None,
        modo: Optional[str] = None,
        monto_min: Optional[float] = None,
        monto_max: Optional[float] = None,
        id_cliente: Optional[str] = None,
        id_transaccion: Optional[str] = None,
        id_seguimiento: Optional[str] = None,
        regla: Optional[str] = None,
        ip: Optional[str] = None,
        dispositivo: Optional[str] = None,
        riesgo_min: Optional[float] = None,
        riesgo_max: Optional[float] = None,
        q: Optional[str] = None,
    ):
        self.es_personal = (
            principal.rol != "comercio"
        )  # analistas y administradores: pueden ver quién decidió; los comercios, no
        if principal.rol == "comercio":
            if comercio_id is not None and comercio_id != principal.comercio_id:
                raise ErrorAPI(404, "No encontrado", "No existe ese comercio.")  # 404 también si es de otro comercio
            comercio_id = principal.comercio_id
        d, h = _fecha(desde, "desde"), _fecha(hasta, "hasta")
        if d and h and d > h:
            raise ErrorAPI(422, "Rango de fechas no válido", "`desde` no puede ser posterior a `hasta`.")
        for nombre, codigo in (("ip", ip), ("dispositivo", dispositivo)):
            if codigo is not None and not PATRON_CODIGO.match(codigo):
                raise ErrorAPI(
                    422,
                    "Código no válido",
                    f"`{nombre}` debe ser un código de 8 caracteres (0-9, A-F), como el que muestra el panel.",
                )
        if decision is not None and decision not in DECISIONES:
            raise ErrorAPI(422, "Decisión no válida", "Usa APROBADA, RECHAZADA o EN_REVISION.")
        if modo is not None and modo not in ("normal", "degradado"):
            raise ErrorAPI(422, "Modo no válido", "Usa normal o degradado.")
        if riesgo_min is not None and riesgo_max is not None and riesgo_min > riesgo_max:
            raise ErrorAPI(422, "Rango de riesgo no válido", "`riesgo_min` no puede superar a `riesgo_max`.")
        self.comercio_id = comercio_id
        self.desde, self.hasta = d, h
        self.decision, self.modo = decision, modo
        self.monto_min, self.monto_max = monto_min, monto_max
        self.id_cliente, self.id_transaccion, self.id_seguimiento = id_cliente, id_transaccion, id_seguimiento
        self.regla, self.ip, self.dispositivo = regla, ip, dispositivo
        self.riesgo_min, self.riesgo_max, self.q = riesgo_min, riesgo_max, q


def _decision_final(e: sqlite3.Row) -> Optional[str]:
    if e["rev_estado"] is None:
        return e["decision"]
    return e["rev_decision"] if e["rev_estado"] == "resuelta" else None


def _nombres_reglas(texto: str) -> List[str]:
    return [r.get("regla", "?") for r in json.loads(texto)]


class ConsultasPanel:
    def __init__(self, almacen: Almacen, servicio: ServicioEvaluacion):
        self.alm, self.srv = almacen, servicio

    # ------------------------------------------------------------------ listado
    def transacciones(self, f: Filtros, orden: str, limite: int, desplazamiento: int) -> Dict:
        with self.alm.lectura() as con:
            total, filas = self.alm.consultas.listar(con, f, orden, limite, desplazamiento)
        return {
            "total": total,
            "transacciones": [
                {
                    "id_evaluacion": e["id"],
                    "id_seguimiento": e["id_seguimiento"],
                    "comercio_id": e["comercio_id"],
                    "comercio": e["comercio_nombre"],
                    "id_transaccion": e["id_transaccion"],
                    "id_cliente": e["id_cliente"],
                    "fecha": e["fecha"],
                    "monto_cop": e["monto_cop"],
                    "decision": e["decision"],
                    "decision_modelo": e["decision_modelo"],
                    "decision_final": _decision_final(e),
                    "puntaje_riesgo": e["probabilidad"],
                    "modo": e["modo"],
                    "reglas": _nombres_reglas(e["reglas_json"]),
                    "ip": e["ip_codigo"],
                    "dispositivo": e["disp_codigo"],
                    "red_origen": e["red_origen"],
                    "estado_origen": e["estado_origen"],
                }
                for e in filas
            ],
        }

    # ------------------------------------------------------------------ detalle: todo de UN pago
    def detalle(self, principal: Principal, id_pago: str) -> Dict:
        with self.alm.lectura(instantanea=True) as con:
            e = self.srv._evaluacion_visible(con, principal, id_pago)  # 404 si no existe o es de otro comercio
            rev = self.alm.consultas.revision_de(con, e["id"])
            res = self.alm.consultas.resultado_de(con, e["id"])
            nombre = self.alm.consultas.nombre_comercio(con, e["comercio_id"])
            audit = self.alm.consultas.auditoria_de(con, e["id"])
            relacionados = self._relacionados(con, principal, e)
        personal = principal.rol != "comercio"
        fila = json.loads(e["fila_json"])
        no_enviados = set(json.loads(e["no_enviados_json"] or "[]"))
        reglas, motivos = json.loads(e["reglas_json"]), json.loads(e["motivos_json"])
        meta = self.srv.meta
        mismo_modelo = (meta.get("modelo"), meta.get("version")) == (e["modelo_nombre"], e["modelo_version"])
        umbrales = (
            {"revisar": meta.get("umbral_revisar"), "bloquear": meta.get("umbral_bloquear")} if mismo_modelo else None
        )
        final = e["decision"] if rev is None else (rev["decision_analista"] if rev["estado"] == "resuelta" else None)
        quien = (
            "analista"
            if rev is not None and rev["estado"] == "resuelta"
            else "pendiente de analista"
            if rev is not None
            else "regla de seguridad"
            if e["decision_modelo"] and e["decision"] != e["decision_modelo"]
            else "modelo"
            if e["modo"] == "normal"
            else "reglas de seguridad en modo degradado"
        )
        return {
            "identificadores": {
                "id_seguimiento": e["id_seguimiento"],
                "id_evaluacion": e["id"],
                "id_transaccion": e["id_transaccion"],
                "comercio_id": e["comercio_id"],
                "comercio": nombre,
                "id_cliente": e["id_cliente"],
            },
            "fechas": {"fecha_pago": e["fecha"], "evaluada_en": e["creada"], "latencia_ms": e["latencia_ms"]},
            "decision": {
                "inicial": e["decision"],
                "del_modelo": e["decision_modelo"],
                "final": final,
                "decidio": quien,
            },
            "puntaje_riesgo": e["probabilidad"],
            "umbrales": umbrales,
            "motivos": motivos,
            "reglas_activadas": reglas,
            "senales": (json.loads(e["senales_json"]) if e["senales_json"] else {"suben": [], "bajan": []})
            if personal
            else None,  # solo para el personal
            "como_se_decidio": self._con_senales(
                self._pasos(e, fila, no_enviados, reglas, motivos, umbrales, rev, res), e, personal
            ),
            "modelo": {
                "nombre": e["modelo_nombre"],
                "version": e["modelo_version"],
                "conjunto_variables": e["modelo_conjunto"],
                "modo": e["modo"],
                "estado_origen": e["estado_origen"],
                "red_origen": e["red_origen"],
            },
            "datos_recibidos": self._datos(fila, no_enviados, e),
            "campos_no_enviados": sorted(no_enviados),
            "revision": None
            if rev is None
            else dict(
                {
                    "estado": rev["estado"],
                    "decision_analista": rev["decision_analista"],
                    "nota": rev["nota"],
                    "creada": rev["creada"],
                    "resuelta": rev["resuelta"],
                },
                **({"resuelta_por": rev["por"] if rev["estado"] == "resuelta" else None} if personal else {}),
            ),
            "resultado_informado": None
            if res is None
            else {"resultado": res["resultado"], "fecha_confirmacion": res["fecha_confirmacion"], "nota": res["nota"]},
            "auditoria": [
                {
                    "momento": a["momento"],
                    "accion": a["accion"],
                    "detalle": json.loads(a["detalle"] or "{}"),
                    "actor": self._actor(a, personal),
                }
                for a in audit
            ],
            "relacionados": relacionados,
        }

    @staticmethod
    def _actor(a: sqlite3.Row, personal: bool) -> str:
        """Quién hizo la acción. El personal ve el nombre; un comercio solo ve el tipo de actor (no se le muestran los nombres de los empleados de la plataforma)."""
        if a["actor_rol"] is None:
            return "el sistema"
        if a["actor_rol"] == "comercio":
            return "tu comercio" if not personal else "el comercio"
        return (
            a["actor_nombre"]
            if personal
            else {"analista": "un analista", "admin": "un administrador"}.get(a["actor_rol"], "la plataforma")
        )

    def _datos(self, fila: Dict, no_enviados: set, e: sqlite3.Row) -> Dict:
        usados = set(EXCLUIDOS) | CAMPOS_FILA_DEL_PAGO
        salida: Dict[str, Dict[str, Any]] = {}
        for grupo, claves in GRUPOS.items():
            salida[grupo] = {}
            for k in claves:
                usados.add(k)
                if k in fila:
                    salida[grupo][k] = {"valor": fila[k], "enviado": k not in no_enviados}
        otros = {k: {"valor": v, "enviado": True} for k, v in fila.items() if k not in usados}
        if otros:
            salida["otros"] = otros
        salida["dispositivo"]["codigo"] = {"valor": e["disp_codigo"], "enviado": e["disp_codigo"] is not None}
        salida["red"]["codigo"] = {"valor": e["ip_codigo"], "enviado": e["ip_codigo"] is not None}
        return salida

    @staticmethod
    def _con_senales(pasos: List[Dict[str, str]], e: sqlite3.Row, personal: bool) -> List[Dict[str, str]]:
        """Para el personal, agrega a la cadena de decisión lo que más empujó el riesgo, justo después del paso del modelo."""
        s = json.loads(e["senales_json"]) if (personal and e["senales_json"]) else None
        if not s or not s["suben"]:
            return pasos
        texto = (
            "Lo que más empujó el riesgo: "
            + "; ".join(f"{x['texto']} (+{x['puntos']:.0f} puntos)" for x in s["suben"])
            + "."
        )
        if s["bajan"]:
            texto += " A favor: " + "; ".join(f"{x['texto']} ({x['puntos']:.0f} puntos)" for x in s["bajan"]) + "."
        i = next((k for k, p in enumerate(pasos) if p["paso"] == "Modelo"), len(pasos) - 1) + 1
        return pasos[:i] + [{"paso": "Señales que más pesaron", "detalle": texto}] + pasos[i:]

    def _pasos(self, e, fila, no_enviados, reglas, motivos, umbrales, rev, res) -> List[Dict[str, str]]:
        pasos = [
            {
                "paso": "Datos recibidos",
                "detalle": f"Llegaron {len(fila) - len(no_enviados)} datos del pago."
                + (
                    f" No se mandaron {len(no_enviados)} ({', '.join(sorted(no_enviados))}); se rellenaron con un valor neutro."
                    if no_enviados
                    else ""
                ),
            }
        ]
        if e["red_origen"].startswith("proveedor"):
            pasos.append(
                {
                    "paso": "Red",
                    "detalle": f"El comercio mandó solo la IP; los datos de red los puso el sistema ({e['red_origen']}).",
                }
            )
        if e["modo"] == "degradado":
            pasos.append(
                {
                    "paso": "Modo degradado",
                    "detalle": (motivos[0] if motivos else "No se pudo usar el modelo.")
                    + " Sin puntaje: no se aprueba a ciegas, queda en revisión salvo que una regla de seguridad rechace.",
                }
            )
        else:
            u = f" (revisar desde {umbrales['revisar']}, bloquear desde {umbrales['bloquear']})" if umbrales else ""
            pasos.append(
                {
                    "paso": "Modelo",
                    "detalle": f"{e['modelo_nombre']} v{e['modelo_version']} calculó una probabilidad de fraude de {e['probabilidad']:.3f}{u}. "
                    f"Con eso el modelo decidió: {e['decision_modelo']}."
                    + (
                        " Usó la variante sin estado del dispositivo (el comercio no lo mandó)."
                        if e["estado_origen"] == "no_enviado"
                        else ""
                    ),
                }
            )
        if reglas:
            for r in reglas:
                pasos.append(
                    {
                        "paso": f"Regla: {r.get('regla', '?')}",
                        "detalle": f"Acción {r.get('accion', '?')}. {r.get('motivo', '')}",
                    }
                )
            if e["decision_modelo"] and e["decision"] != e["decision_modelo"]:
                pasos.append(
                    {
                        "paso": "Efecto de las reglas",
                        "detalle": f"Cambiaron la decisión del modelo ({e['decision_modelo']}) a {e['decision']}.",
                    }
                )
            else:
                pasos.append({"paso": "Efecto de las reglas", "detalle": "No cambiaron la decisión."})
        else:
            pasos.append({"paso": "Reglas de seguridad", "detalle": "Ninguna se activó."})
        pasos.append({"paso": "Decisión inicial", "detalle": e["decision"]})
        if rev is not None:
            pasos.append(
                {
                    "paso": "Revisión manual",
                    "detalle": (
                        "Pendiente de un analista."
                        if rev["estado"] == "pendiente"
                        else f"Un analista resolvió: {rev['decision_analista']}."
                        + (f" Nota: {rev['nota']}" if rev["nota"] else "")
                    ),
                }
            )
        if res is not None:
            pasos.append({"paso": "Resultado posterior", "detalle": f"El comercio informó: {res['resultado']}."})
        return pasos

    def _relacionados(self, con: sqlite3.Connection, principal: Principal, e: sqlite3.Row) -> Dict:
        alcance = (
            principal.comercio_id if principal.rol == "comercio" else None
        )  # un comercio solo cuenta sus propios pagos

        def grupo(columna: str, valor: Optional[str], clave_filtro: str) -> Optional[Dict]:
            if valor is None:
                return None
            n, ultimos = self.alm.consultas.pagos_con_codigo(con, columna, valor, e["id"], alcance)
            return {
                "codigo": valor,
                "otros_pagos": n,
                "filtro": {clave_filtro: valor},
                "ultimos": [
                    {
                        "id_seguimiento": u["id_seguimiento"],
                        "id_cliente": u["id_cliente"],
                        "decision": u["decision"],
                        "fecha": u["fecha"],
                    }
                    for u in ultimos
                ],
            }

        cliente = self.alm.consultas.otros_pagos_del_cliente(con, e["comercio_id"], e["id_cliente"], e["id"])
        return {
            "misma_ip": grupo("ip_codigo", e["ip_codigo"], "ip"),
            "mismo_dispositivo": grupo("disp_codigo", e["disp_codigo"], "dispositivo"),
            "mismo_cliente": {
                "id_cliente": e["id_cliente"],
                "otros_pagos": cliente,
                "filtro": {"id_cliente": e["id_cliente"], "comercio_id": e["comercio_id"]},
            },
        }

    # ------------------------------------------------------------------ resumen
    def resumen(self, f: Filtros) -> Dict:
        with self.alm.lectura(instantanea=True) as con:
            datos = self.alm.consultas.resumen(con, f, f.es_personal)
        t, p95, pendientes = datos["totales"], datos["p95"], datos["pendientes"]
        n = t["pagos"] or 0
        por_dia, por_comercio, reglas, hist = (
            datos["por_dia"],
            datos["por_comercio"],
            datos["reglas"],
            datos["histograma"],
        )
        por_analista = [dict(r) for r in datos["por_analista"]]
        cubetas = {h["cubeta"]: h["pagos"] for h in hist}
        return {
            "totales": {
                "pagos": n,
                "aprobadas": t["aprobadas"] or 0,
                "en_revision": t["en_revision"] or 0,
                "rechazadas": t["rechazadas"] or 0,
                "monto_cop": t["monto"],
                "monto_rechazado_cop": t["monto_rechazado"],
                "monto_en_revision_cop": t["monto_en_revision"],
                "modo_degradado": t["degradados"] or 0,
                "sin_estado_del_dispositivo": t["sin_estado"] or 0,
                "revisiones_pendientes": pendientes,
                "latencia_media_ms": t["latencia_media"],
                "latencia_p95_ms": p95,
            },
            "por_dia": [dict(d) for d in por_dia],
            "por_comercio": [dict(c) for c in por_comercio],
            "reglas_mas_activadas": [dict(r) for r in reglas],
            "por_analista": por_analista,
            "distribucion_del_puntaje": [
                {"desde": i / 10, "hasta": (i + 1) / 10, "pagos": cubetas.get(i, 0)} for i in range(10)
            ],
        }

    # ------------------------------------------------------------------ vínculos: patrones entre pagos
    def vinculos(self, f: Filtros, minimo: int, ventana_minutos: int = 10) -> Dict:
        with self.alm.lectura(instantanea=True) as con:
            v = self.alm.consultas.vinculos(con, f, minimo)
        ips, disp, clientes, candidatas = v["ips"], v["dispositivos"], v["clientes"], v["candidatas"]
        return {
            "minimo": minimo,
            "misma_ip_varios_clientes": [dict(r, filtro={"ip": r["codigo"]}) for r in ips],
            "mismo_dispositivo_varios_clientes": [dict(r, filtro={"dispositivo": r["codigo"]}) for r in disp],
            "cliente_con_varias_ips": [
                dict(r, filtro={"id_cliente": r["id_cliente"], "comercio_id": r["comercio_id"]}) for r in clientes
            ],
            "rafagas_por_ip": self._rafagas(candidatas, minimo, ventana_minutos),
            "ventana_rafaga_minutos": ventana_minutos,
        }

    @staticmethod
    def _rafagas(filas: List[sqlite3.Row], minimo: int, minutos: int) -> List[Dict]:
        """IP con `minimo` pagos o más dentro de una ventana de `minutos` minutos (ventana deslizante sobre los pagos de cada IP)."""
        from datetime import datetime

        por_ip: Dict[str, List[datetime]] = {}
        for r in filas:
            try:
                por_ip.setdefault(r["ip_codigo"], []).append(datetime.fromisoformat(r["fecha"]))
            except ValueError:
                continue
        salida = []
        for codigo, fechas in por_ip.items():
            mejor, ini = 0, 0
            for fin in range(len(fechas)):
                while (fechas[fin] - fechas[ini]).total_seconds() > minutos * 60:
                    ini += 1
                mejor = max(mejor, fin - ini + 1)
            if mejor >= minimo:
                salida.append(
                    {
                        "codigo": codigo,
                        "pagos_en_ventana": mejor,
                        "pagos_totales": len(fechas),
                        "filtro": {"ip": codigo},
                    }
                )
        return sorted(salida, key=lambda x: (-x["pagos_en_ventana"], x["codigo"]))[:50]

    # ------------------------------------------------------------------ comercios (para el filtro)
    def comercios(self) -> List[Dict]:
        with self.alm.lectura() as con:
            return [dict(r) for r in self.alm.consultas.comercios_con_pagos(con)]

    # ------------------------------------------------------------------ deriva del modelo y modelos registrados
    def _calculo_deriva(self, ventana_dias: int, minimo: int) -> Dict:
        meta = self.srv.meta
        desde = (datetime.now() - timedelta(days=ventana_dias)).isoformat(
            timespec="microseconds"
        )  # `creada` se escribe con la hora real del servidor
        with self.alm.lectura(instantanea=True) as con:
            cuentas = self.alm.consultas.distribucion_puntaje(
                con, meta["modelo"], meta["version"], meta["conjunto"], desde
            )
        registro = self.alm.registro_modelos.en_uso("principal")
        ref = (registro or {}).get("referencia") or meta.get(
            "referencia_puntaje"
        )  # primero la del registro (la que se verificó), luego la que trae el propio modelo
        ref_props = ref["proporciones"] if ref and len(ref.get("proporciones") or []) == deriva.CUBETAS else None
        pagos = sum(cuentas)
        obs_props = deriva.proporciones(cuentas) if pagos else None
        valor = deriva.psi(ref_props, obs_props) if ref_props and obs_props else None
        if ref_props is None:
            estado = "sin_referencia"
        elif pagos == 0:
            estado = "sin_pagos"
        else:
            estado = deriva.estado(valor, pagos, minimo)
        return {
            "meta": meta,
            "desde": desde,
            "cuentas": cuentas,
            "pagos": pagos,
            "ref": ref,
            "ref_props": ref_props,
            "obs_props": obs_props,
            "psi": valor,
            "estado": estado,
        }

    def deriva(self, ventana_dias: int, minimo: int, huella_verificada: bool) -> Dict:
        c = self._calculo_deriva(ventana_dias, minimo)
        cubetas = [
            {
                "desde": i / deriva.CUBETAS,
                "hasta": (i + 1) / deriva.CUBETAS,
                "referencia": c["ref_props"][i] if c["ref_props"] else None,
                "observada": c["obs_props"][i] if c["obs_props"] else None,
            }
            for i in range(deriva.CUBETAS)
        ]
        notas = {
            "sin_referencia": "Este modelo no trae la distribución de referencia de sus puntajes (se guarda al entrenar). Calcúlala con «admin modelo-referencia».",
            "sin_pagos": "Todavía no hay pagos evaluados por este modelo en la ventana.",
            "muestra_insuficiente": f"Hay menos de {minimo} pagos en la ventana: el PSI todavía es ruido y no se da un estado.",
            "estable": "Los puntajes se parecen a los de la validación.",
            "atencion": "Los puntajes empiezan a diferir de los de la validación: conviene mirarlo.",
            "alerta": "Los puntajes cambiaron bastante frente a la validación: revisa los datos de entrada y considera reentrenar.",
        }
        return {
            "modelo": {
                "nombre": c["meta"]["modelo"],
                "version": c["meta"]["version"],
                "conjunto": c["meta"]["conjunto"],
                "huella_verificada": huella_verificada,
            },
            "referencia": {"disponible": c["ref_props"] is not None, "pagos": (c["ref"] or {}).get("n")},
            "ventana": {"dias": ventana_dias, "desde": c["desde"], "pagos": c["pagos"], "minimo_requerido": minimo},
            "psi": c["psi"],
            "estado": c["estado"],
            "umbrales": {"atencion": deriva.UMBRAL_ATENCION, "alerta": deriva.UMBRAL_ALERTA},
            "cubetas": cubetas,
            "nota": notas[c["estado"]]
            + " Mide si los PUNTAJES cambiaron, no si el modelo acierta; con datos sintéticos solo demuestra el mecanismo.",
        }

    def lineas_deriva_prometheus(self, ventana_dias: int, minimo: int) -> str:
        """Dos series en el formato de texto de Prometheus (se calculan al leer, con la base: valen igual con varios procesos)."""
        c = self._calculo_deriva(ventana_dias, minimo)
        salida = [
            "# HELP antifraude_deriva_pagos Pagos evaluados por el modelo en uso en la ventana de deriva.",
            "# TYPE antifraude_deriva_pagos gauge",
            f"antifraude_deriva_pagos {c['pagos']}",
        ]
        if c["psi"] is not None:
            salida += [
                "# HELP antifraude_deriva_psi PSI de los puntajes de la ventana frente a la referencia de validación (con muestra insuficiente es solo informativo).",
                "# TYPE antifraude_deriva_psi gauge",
                f"antifraude_deriva_psi {c['psi']:.6f}",
            ]
        return "\n".join(salida) + "\n"

    def modelos(self, info_modelos: Dict) -> List[Dict]:
        """El registro de modelos para el personal, marcando cuál se está ejecutando y con huella verificada."""
        filas = []
        for m in self.alm.registro_modelos.listar():
            vivo = info_modelos.get(m["rol"]) or {}
            filas.append(
                {
                    "id": m["id"],
                    "rol": m["rol"],
                    "estado": m["estado"],
                    "nombre": m["nombre"],
                    "version": m["version"],
                    "conjunto": m["conjunto"],
                    "huella": m["sha256"][:12],
                    "registrado": m["registrado"],
                    "promovido": m["promovido"],
                    "archivado": m["archivado"],
                    "tiene_referencia": m["referencia"] is not None,
                    "en_ejecucion": bool(vivo.get("verificado")) and vivo.get("id") == m["id"],
                }
            )
        return filas
