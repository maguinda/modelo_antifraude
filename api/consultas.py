"""Consultas de SOLO LECTURA para el panel de control: listado con filtros, detalle completo de un pago, resumen y vínculos.

Reglas que se cumplen aquí (y que las pruebas vigilan):
- Todo es lectura: ninguna consulta escribe en la base.
- Un comercio solo ve lo suyo (si pide el de otro, 404 como en el resto de la API). Analistas y administradores ven todos.
- Nada del usuario entra al SQL como texto: los valores van como parámetros y el orden sale de una lista fija.
- La IP y la huella del dispositivo NUNCA se muestran: solo códigos cortos (p. ej. `7F3A91C2`) que permiten reconocer repeticiones.
"""
import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .almacen import Almacen, Principal
from .servicio import ErrorAPI, ServicioEvaluacion

ORDENES = {"fecha_desc": "e.fecha DESC, e.id", "fecha_asc": "e.fecha ASC, e.id", "monto_desc": "e.monto_cop DESC, e.id",
           "riesgo_desc": "e.probabilidad DESC, e.id"}
DECISIONES = ("APROBADA", "RECHAZADA", "EN_REVISION")
PATRON_CODIGO = re.compile(r"^[0-9A-Fa-f]{8}$")
EXCLUIDOS = {"ip_huella", "huella_dispositivo"}              # nunca se muestran: se reemplazan por códigos cortos
GRUPOS = {
    "pago": ["monto_cop", "metodo_pago", "canal_pago", "categoria", "localizacion", "fecha", "hora", "mes"],
    "dispositivo": ["dispositivo", "estado_dispositivo", "sistema_operativo", "navegador", "resolucion_pantalla", "zona_horaria_dispositivo", "es_emulador"],
    "red": ["tipo_ip", "ip_pais", "ip_ciudad_estimada", "distancia_ip_ciudad_km", "ip_es_datacenter", "ip_reputacion"],
    "identidad": ["kyc_nivel", "edad_cuenta_dias", "autenticacion_2fa", "intentos_fallidos_24h", "cambio_credenciales_7d"],
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
    """Convierte los filtros pedidos en un WHERE con parámetros (nunca texto del usuario dentro del SQL)."""

    def __init__(self, principal: Principal, comercio_id: Optional[int] = None, desde: Optional[str] = None, hasta: Optional[str] = None,
                 decision: Optional[str] = None, modo: Optional[str] = None, monto_min: Optional[float] = None, monto_max: Optional[float] = None,
                 id_cliente: Optional[str] = None, id_transaccion: Optional[str] = None, id_seguimiento: Optional[str] = None,
                 regla: Optional[str] = None, ip: Optional[str] = None, dispositivo: Optional[str] = None,
                 riesgo_min: Optional[float] = None, riesgo_max: Optional[float] = None, q: Optional[str] = None):
        self.es_personal = principal.rol != "comercio"                     # analistas y administradores: pueden ver quién decidió; los comercios, no
        if principal.rol == "comercio":
            if comercio_id is not None and comercio_id != principal.comercio_id:
                raise ErrorAPI(404, "No encontrado", "No existe ese comercio.")               # 404 también si es de otro comercio
            comercio_id = principal.comercio_id
        d, h = _fecha(desde, "desde"), _fecha(hasta, "hasta")
        if d and h and d > h:
            raise ErrorAPI(422, "Rango de fechas no válido", "`desde` no puede ser posterior a `hasta`.")
        for nombre, codigo in (("ip", ip), ("dispositivo", dispositivo)):
            if codigo is not None and not PATRON_CODIGO.match(codigo):
                raise ErrorAPI(422, "Código no válido", f"`{nombre}` debe ser un código de 8 caracteres (0-9, A-F), como el que muestra el panel.")
        if decision is not None and decision not in DECISIONES:
            raise ErrorAPI(422, "Decisión no válida", "Usa APROBADA, RECHAZADA o EN_REVISION.")
        if modo is not None and modo not in ("normal", "degradado"):
            raise ErrorAPI(422, "Modo no válido", "Usa normal o degradado.")
        cond: List[str] = []
        par: List[Any] = []

        def agregar(sql: str, *valores: Any) -> None:
            cond.append(sql)
            par.extend(valores)
        if comercio_id is not None:
            agregar("e.comercio_id = ?", comercio_id)
        if d:
            agregar("e.fecha >= ?", d.isoformat())
        if h:
            agregar("e.fecha < ?", (h + timedelta(days=1)).isoformat())
        if decision:
            agregar("e.decision = ?", decision)
        if modo:
            agregar("e.modo = ?", modo)
        if monto_min is not None:
            agregar("e.monto_cop >= ?", monto_min)
        if monto_max is not None:
            agregar("e.monto_cop <= ?", monto_max)
        if id_cliente:
            agregar("e.id_cliente = ?", id_cliente)
        if id_transaccion:
            agregar("e.id_transaccion = ?", id_transaccion)
        if id_seguimiento:
            agregar("e.id_seguimiento = ?", id_seguimiento.upper())
        if regla:
            agregar("EXISTS (SELECT 1 FROM json_each(e.reglas_json) j WHERE json_extract(j.value, '$.regla') = ?)", regla)
        if riesgo_min is not None and riesgo_max is not None and riesgo_min > riesgo_max:
            raise ErrorAPI(422, "Rango de riesgo no válido", "`riesgo_min` no puede superar a `riesgo_max`.")
        if riesgo_min is not None:
            agregar("e.probabilidad >= ?", riesgo_min)               # los pagos sin puntaje (modo degradado) no entran en un filtro de riesgo
        if riesgo_max is not None:
            agregar("e.probabilidad <= ?", riesgo_max)
        if q:
            agregar("(e.id_seguimiento = ? OR e.id_transaccion = ? OR e.id_cliente = ?)", q.upper(), q, q)
        if ip:
            agregar("e.ip_codigo = ?", ip.upper())
        if dispositivo:
            agregar("e.disp_codigo = ?", dispositivo.upper())
        self.condiciones = cond
        self.donde = ("WHERE " + " AND ".join(cond)) if cond else ""
        self.parametros = tuple(par)
        self.comercio_id = comercio_id


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
            total = con.execute(f"SELECT COUNT(*) FROM evaluaciones e {f.donde}", f.parametros).fetchone()[0]
            filas = con.execute(
                "SELECT e.*, c.nombre AS comercio_nombre, r.estado AS rev_estado, r.decision_analista AS rev_decision "
                f"FROM evaluaciones e JOIN comercios c ON c.id = e.comercio_id LEFT JOIN revisiones r ON r.id_evaluacion = e.id {f.donde} "
                f"ORDER BY {ORDENES[orden]} LIMIT ? OFFSET ?", f.parametros + (limite, desplazamiento)).fetchall()
        return {"total": total, "transacciones": [{
            "id_evaluacion": e["id"], "id_seguimiento": e["id_seguimiento"], "comercio_id": e["comercio_id"], "comercio": e["comercio_nombre"],
            "id_transaccion": e["id_transaccion"], "id_cliente": e["id_cliente"], "fecha": e["fecha"], "monto_cop": e["monto_cop"],
            "decision": e["decision"], "decision_modelo": e["decision_modelo"], "decision_final": _decision_final(e),
            "puntaje_riesgo": e["probabilidad"], "modo": e["modo"], "reglas": _nombres_reglas(e["reglas_json"]),
            "ip": e["ip_codigo"], "dispositivo": e["disp_codigo"], "red_origen": e["red_origen"], "estado_origen": e["estado_origen"]} for e in filas]}

    # ------------------------------------------------------------------ detalle: todo de UN pago
    def detalle(self, principal: Principal, id_pago: str) -> Dict:
        with self.alm.lectura(instantanea=True) as con:
            e = self.srv._evaluacion_visible(con, principal, id_pago)                    # 404 si no existe o es de otro comercio
            rev = con.execute("SELECT r.*, COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS por FROM revisiones r LEFT JOIN claves k ON k.id = r.clave_analista "
                              "WHERE r.id_evaluacion=?", (e["id"],)).fetchone()
            res = con.execute("SELECT * FROM resultados WHERE id_evaluacion=?", (e["id"],)).fetchone()
            nombre = con.execute("SELECT nombre FROM comercios WHERE id=?", (e["comercio_id"],)).fetchone()[0]
            audit = con.execute("SELECT a.momento, a.accion, a.detalle, k.rol AS actor_rol, COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS actor_nombre "
                                "FROM auditoria a LEFT JOIN claves k ON k.id = a.clave_id WHERE a.id_evaluacion=? ORDER BY a.id", (e["id"],)).fetchall()
            relacionados = self._relacionados(con, principal, e)
        personal = principal.rol != "comercio"
        fila = json.loads(e["fila_json"])
        no_enviados = set(json.loads(e["no_enviados_json"] or "[]"))
        reglas, motivos = json.loads(e["reglas_json"]), json.loads(e["motivos_json"])
        meta = self.srv.meta
        mismo_modelo = (meta.get("modelo"), meta.get("version")) == (e["modelo_nombre"], e["modelo_version"])
        umbrales = {"revisar": meta.get("umbral_revisar"), "bloquear": meta.get("umbral_bloquear")} if mismo_modelo else None
        final = e["decision"] if rev is None else (rev["decision_analista"] if rev["estado"] == "resuelta" else None)
        quien = ("analista" if rev is not None and rev["estado"] == "resuelta" else
                 "pendiente de analista" if rev is not None else
                 "regla de seguridad" if e["decision_modelo"] and e["decision"] != e["decision_modelo"] else
                 "modelo" if e["modo"] == "normal" else "reglas de seguridad en modo degradado")
        return {
            "identificadores": {"id_seguimiento": e["id_seguimiento"], "id_evaluacion": e["id"], "id_transaccion": e["id_transaccion"],
                                "comercio_id": e["comercio_id"], "comercio": nombre, "id_cliente": e["id_cliente"]},
            "fechas": {"fecha_pago": e["fecha"], "evaluada_en": e["creada"], "latencia_ms": e["latencia_ms"]},
            "decision": {"inicial": e["decision"], "del_modelo": e["decision_modelo"], "final": final, "decidio": quien},
            "puntaje_riesgo": e["probabilidad"], "umbrales": umbrales, "motivos": motivos, "reglas_activadas": reglas,
            "senales": (json.loads(e["senales_json"]) if e["senales_json"] else {"suben": [], "bajan": []}) if personal else None,       # solo para el personal
            "como_se_decidio": self._con_senales(self._pasos(e, fila, no_enviados, reglas, motivos, umbrales, rev, res), e, personal),
            "modelo": {"nombre": e["modelo_nombre"], "version": e["modelo_version"], "conjunto_variables": e["modelo_conjunto"], "modo": e["modo"],
                       "estado_origen": e["estado_origen"], "red_origen": e["red_origen"]},
            "datos_recibidos": self._datos(fila, no_enviados, e),
            "campos_no_enviados": sorted(no_enviados),
            "revision": None if rev is None else dict({"estado": rev["estado"], "decision_analista": rev["decision_analista"], "nota": rev["nota"],
                                                       "creada": rev["creada"], "resuelta": rev["resuelta"]},
                                                      **({"resuelta_por": rev["por"] if rev["estado"] == "resuelta" else None} if personal else {})),
            "resultado_informado": None if res is None else {"resultado": res["resultado"], "fecha_confirmacion": res["fecha_confirmacion"], "nota": res["nota"]},
            "auditoria": [{"momento": a["momento"], "accion": a["accion"], "detalle": json.loads(a["detalle"] or "{}"),
                           "actor": self._actor(a, personal)} for a in audit],
            "relacionados": relacionados}

    @staticmethod
    def _actor(a: sqlite3.Row, personal: bool) -> str:
        """Quién hizo la acción. El personal ve el nombre; un comercio solo ve el tipo de actor (no se le muestran los nombres de los empleados de la plataforma)."""
        if a["actor_rol"] is None:
            return "el sistema"
        if a["actor_rol"] == "comercio":
            return "tu comercio" if not personal else "el comercio"
        return a["actor_nombre"] if personal else {"analista": "un analista", "admin": "un administrador"}.get(a["actor_rol"], "la plataforma")

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
        texto = "Lo que más empujó el riesgo: " + "; ".join(f"{x['texto']} (+{x['puntos']:.0f} puntos)" for x in s["suben"]) + "."
        if s["bajan"]:
            texto += " A favor: " + "; ".join(f"{x['texto']} ({x['puntos']:.0f} puntos)" for x in s["bajan"]) + "."
        i = next((k for k, p in enumerate(pasos) if p["paso"] == "Modelo"), len(pasos) - 1) + 1
        return pasos[:i] + [{"paso": "Señales que más pesaron", "detalle": texto}] + pasos[i:]

    def _pasos(self, e, fila, no_enviados, reglas, motivos, umbrales, rev, res) -> List[Dict[str, str]]:
        pasos = [{"paso": "Datos recibidos", "detalle": f"Llegaron {len(fila) - len(no_enviados)} datos del pago." + (
            f" No se mandaron {len(no_enviados)} ({', '.join(sorted(no_enviados))}); se rellenaron con un valor neutro." if no_enviados else "")}]
        if e["red_origen"].startswith("proveedor"):
            pasos.append({"paso": "Red", "detalle": f"El comercio mandó solo la IP; los datos de red los puso el sistema ({e['red_origen']})."})
        if e["modo"] == "degradado":
            pasos.append({"paso": "Modo degradado", "detalle": (motivos[0] if motivos else "No se pudo usar el modelo.") +
                          " Sin puntaje: no se aprueba a ciegas, queda en revisión salvo que una regla de seguridad rechace."})
        else:
            u = f" (revisar desde {umbrales['revisar']}, bloquear desde {umbrales['bloquear']})" if umbrales else ""
            pasos.append({"paso": "Modelo", "detalle": f"{e['modelo_nombre']} v{e['modelo_version']} calculó una probabilidad de fraude de {e['probabilidad']:.3f}{u}. "
                          f"Con eso el modelo decidió: {e['decision_modelo']}." + (" Usó la variante sin estado del dispositivo (el comercio no lo mandó)." if e["estado_origen"] == "no_enviado" else "")})
        if reglas:
            for r in reglas:
                pasos.append({"paso": f"Regla: {r.get('regla', '?')}", "detalle": f"Acción {r.get('accion', '?')}. {r.get('motivo', '')}"})
            if e["decision_modelo"] and e["decision"] != e["decision_modelo"]:
                pasos.append({"paso": "Efecto de las reglas", "detalle": f"Cambiaron la decisión del modelo ({e['decision_modelo']}) a {e['decision']}."})
            else:
                pasos.append({"paso": "Efecto de las reglas", "detalle": "No cambiaron la decisión."})
        else:
            pasos.append({"paso": "Reglas de seguridad", "detalle": "Ninguna se activó."})
        pasos.append({"paso": "Decisión inicial", "detalle": e["decision"]})
        if rev is not None:
            pasos.append({"paso": "Revisión manual", "detalle": ("Pendiente de un analista." if rev["estado"] == "pendiente" else
                          f"Un analista resolvió: {rev['decision_analista']}." + (f" Nota: {rev['nota']}" if rev["nota"] else ""))})
        if res is not None:
            pasos.append({"paso": "Resultado posterior", "detalle": f"El comercio informó: {res['resultado']}."})
        return pasos

    def _relacionados(self, con: sqlite3.Connection, principal: Principal, e: sqlite3.Row) -> Dict:
        alcance, par = ("", ()) if principal.rol != "comercio" else (" AND comercio_id = ?", (principal.comercio_id,))

        def grupo(columna: str, valor: Optional[str], clave_filtro: str) -> Optional[Dict]:
            if valor is None:
                return None
            n = con.execute(f"SELECT COUNT(*) FROM evaluaciones WHERE {columna} = ? AND id != ?{alcance}", (valor, e["id"]) + par).fetchone()[0]
            ultimos = con.execute(f"SELECT id_seguimiento, id_cliente, decision, fecha FROM evaluaciones WHERE {columna} = ? AND id != ?{alcance} "
                                  "ORDER BY fecha DESC LIMIT 5", (valor, e["id"]) + par).fetchall()
            return {"codigo": valor, "otros_pagos": n, "filtro": {clave_filtro: valor},
                    "ultimos": [{"id_seguimiento": u["id_seguimiento"], "id_cliente": u["id_cliente"], "decision": u["decision"], "fecha": u["fecha"]} for u in ultimos]}
        cliente = con.execute("SELECT COUNT(*) FROM evaluaciones WHERE comercio_id = ? AND id_cliente = ? AND id != ?", (e["comercio_id"], e["id_cliente"], e["id"])).fetchone()[0]
        return {"misma_ip": grupo("ip_codigo", e["ip_codigo"], "ip"), "mismo_dispositivo": grupo("disp_codigo", e["disp_codigo"], "dispositivo"),
                "mismo_cliente": {"id_cliente": e["id_cliente"], "otros_pagos": cliente, "filtro": {"id_cliente": e["id_cliente"], "comercio_id": e["comercio_id"]}}}

    # ------------------------------------------------------------------ resumen
    def resumen(self, f: Filtros) -> Dict:
        with self.alm.lectura(instantanea=True) as con:
            t = con.execute(
                "SELECT COUNT(*) pagos, SUM(decision='APROBADA') aprobadas, SUM(decision='EN_REVISION') en_revision, SUM(decision='RECHAZADA') rechazadas, "
                "COALESCE(SUM(monto_cop),0) monto, COALESCE(SUM(CASE WHEN decision='RECHAZADA' THEN monto_cop END),0) monto_rechazado, "
                "COALESCE(SUM(CASE WHEN decision='EN_REVISION' THEN monto_cop END),0) monto_en_revision, "
                "SUM(modo='degradado') degradados, SUM(estado_origen='no_enviado') sin_estado, AVG(latencia_ms) latencia_media "
                f"FROM evaluaciones e {f.donde}", f.parametros).fetchone()
            n = t["pagos"] or 0
            p95 = None
            if n:
                p95 = con.execute(f"SELECT latencia_ms FROM evaluaciones e {f.donde} ORDER BY latencia_ms LIMIT 1 OFFSET ?",
                                  f.parametros + (int(0.95 * (n - 1)),)).fetchone()[0]
            por_dia = con.execute(
                "SELECT substr(fecha,1,10) dia, SUM(decision='APROBADA') aprobadas, SUM(decision='EN_REVISION') en_revision, SUM(decision='RECHAZADA') rechazadas "
                f"FROM evaluaciones e {f.donde} GROUP BY dia ORDER BY dia", f.parametros).fetchall()
            por_comercio = con.execute(
                "SELECT e.comercio_id, c.nombre, COUNT(*) pagos, SUM(decision='APROBADA') aprobadas, SUM(decision='EN_REVISION') en_revision, "
                "SUM(decision='RECHAZADA') rechazadas, COALESCE(SUM(CASE WHEN decision='RECHAZADA' THEN monto_cop END),0) monto_rechazado "
                f"FROM evaluaciones e JOIN comercios c ON c.id = e.comercio_id {f.donde} GROUP BY e.comercio_id ORDER BY pagos DESC LIMIT 100", f.parametros).fetchall()
            reglas = con.execute(
                "SELECT json_extract(j.value, '$.regla') regla, COUNT(*) veces, SUM(e.decision='RECHAZADA') rechazadas "
                f"FROM evaluaciones e, json_each(e.reglas_json) j {f.donde} GROUP BY regla ORDER BY veces DESC LIMIT 20", f.parametros).fetchall()
            hist = con.execute(
                "SELECT MIN(CAST(probabilidad * 10 AS INTEGER), 9) cubeta, COUNT(*) pagos "
                f"FROM evaluaciones e {f.donde + (' AND ' if f.donde else 'WHERE ')} probabilidad IS NOT NULL GROUP BY cubeta", f.parametros).fetchall()
            por_analista = []
            if f.es_personal:
                ya_hay = f.donde + (" AND " if f.donde else "WHERE ")
                por_analista = [dict(r) for r in con.execute(
                    "SELECT COALESCE(NULLIF(k.etiqueta, ''), 'clave #' || k.id) AS analista, COUNT(*) AS resueltas, SUM(r.decision_analista = 'APROBADA') AS aprobadas, "
                    "SUM(r.decision_analista = 'RECHAZADA') AS rechazadas, ROUND(AVG((julianday(r.resuelta) - julianday(r.creada)) * 1440), 1) AS minutos_promedio "
                    f"FROM revisiones r JOIN evaluaciones e ON e.id = r.id_evaluacion LEFT JOIN claves k ON k.id = r.clave_analista {ya_hay} r.estado = 'resuelta' "
                    "GROUP BY r.clave_analista ORDER BY resueltas DESC LIMIT 50", f.parametros)]
            alcance, par = ("WHERE e.comercio_id = ?", (f.comercio_id,)) if f.comercio_id is not None else ("", ())
            pendientes = con.execute(f"SELECT COUNT(*) FROM revisiones r JOIN evaluaciones e ON e.id = r.id_evaluacion {alcance + (' AND ' if alcance else 'WHERE ')} r.estado = 'pendiente'", par).fetchone()[0]
        cubetas = {h["cubeta"]: h["pagos"] for h in hist}
        return {"totales": {"pagos": n, "aprobadas": t["aprobadas"] or 0, "en_revision": t["en_revision"] or 0, "rechazadas": t["rechazadas"] or 0,
                            "monto_cop": t["monto"], "monto_rechazado_cop": t["monto_rechazado"], "monto_en_revision_cop": t["monto_en_revision"],
                            "modo_degradado": t["degradados"] or 0, "sin_estado_del_dispositivo": t["sin_estado"] or 0,
                            "revisiones_pendientes": pendientes, "latencia_media_ms": t["latencia_media"], "latencia_p95_ms": p95},
                "por_dia": [dict(d) for d in por_dia], "por_comercio": [dict(c) for c in por_comercio],
                "reglas_mas_activadas": [dict(r) for r in reglas], "por_analista": por_analista,
                "distribucion_del_puntaje": [{"desde": i / 10, "hasta": (i + 1) / 10, "pagos": cubetas.get(i, 0)} for i in range(10)]}

    # ------------------------------------------------------------------ vínculos: patrones entre pagos
    def vinculos(self, f: Filtros, minimo: int, ventana_minutos: int = 10) -> Dict:
        base = f.donde + (" AND " if f.donde else "WHERE ")
        with self.alm.lectura(instantanea=True) as con:
            ips = con.execute(
                "SELECT e.ip_codigo codigo, COUNT(*) pagos, COUNT(DISTINCT e.comercio_id || '|' || e.id_cliente) clientes, COUNT(DISTINCT e.comercio_id) comercios, "
                "SUM(decision='RECHAZADA') rechazadas, SUM(decision='EN_REVISION') en_revision, MIN(fecha) primera, MAX(fecha) ultima "
                f"FROM evaluaciones e {base} e.ip_codigo IS NOT NULL GROUP BY e.ip_codigo HAVING clientes >= ? ORDER BY clientes DESC, pagos DESC LIMIT 50",
                f.parametros + (minimo,)).fetchall()
            disp = con.execute(
                "SELECT e.disp_codigo codigo, COUNT(*) pagos, COUNT(DISTINCT e.comercio_id || '|' || e.id_cliente) clientes, COUNT(DISTINCT e.comercio_id) comercios, "
                "SUM(decision='RECHAZADA') rechazadas, SUM(decision='EN_REVISION') en_revision, MIN(fecha) primera, MAX(fecha) ultima "
                f"FROM evaluaciones e {base} e.disp_codigo IS NOT NULL GROUP BY e.disp_codigo HAVING clientes >= ? ORDER BY clientes DESC, pagos DESC LIMIT 50",
                f.parametros + (minimo,)).fetchall()
            clientes = con.execute(
                "SELECT e.comercio_id, c.nombre comercio, e.id_cliente, COUNT(*) pagos, COUNT(DISTINCT e.ip_codigo) ips, "
                "SUM(decision='RECHAZADA') rechazadas, MIN(fecha) primera, MAX(fecha) ultima "
                f"FROM evaluaciones e JOIN comercios c ON c.id = e.comercio_id {base} e.ip_codigo IS NOT NULL "
                "GROUP BY e.comercio_id, e.id_cliente HAVING ips >= ? ORDER BY ips DESC, pagos DESC LIMIT 50", f.parametros + (minimo,)).fetchall()
            candidatas = con.execute(
                f"SELECT e.ip_codigo, e.fecha FROM evaluaciones e {base} e.ip_codigo IN ("
                f"SELECT ip_codigo FROM evaluaciones e {f.donde + (' AND ' if f.donde else 'WHERE ')} e.ip_codigo IS NOT NULL GROUP BY ip_codigo HAVING COUNT(*) >= ? LIMIT 200) "
                "ORDER BY e.ip_codigo, e.fecha", f.parametros + f.parametros + (minimo,)).fetchall()
        return {"minimo": minimo,
                "misma_ip_varios_clientes": [dict(r, filtro={"ip": r["codigo"]}) for r in ips],
                "mismo_dispositivo_varios_clientes": [dict(r, filtro={"dispositivo": r["codigo"]}) for r in disp],
                "cliente_con_varias_ips": [dict(r, filtro={"id_cliente": r["id_cliente"], "comercio_id": r["comercio_id"]}) for r in clientes],
                "rafagas_por_ip": self._rafagas(candidatas, minimo, ventana_minutos), "ventana_rafaga_minutos": ventana_minutos}

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
                salida.append({"codigo": codigo, "pagos_en_ventana": mejor, "pagos_totales": len(fechas), "filtro": {"ip": codigo}})
        return sorted(salida, key=lambda x: (-x["pagos_en_ventana"], x["codigo"]))[:50]

    # ------------------------------------------------------------------ comercios (para el filtro)
    def comercios(self) -> List[Dict]:
        with self.alm.lectura() as con:
            return [dict(r) for r in con.execute(
                "SELECT c.id, c.nombre, COUNT(e.id) pagos FROM comercios c LEFT JOIN evaluaciones e ON e.comercio_id = c.id "
                "WHERE c.id != 1 GROUP BY c.id ORDER BY pagos DESC, c.nombre")]
