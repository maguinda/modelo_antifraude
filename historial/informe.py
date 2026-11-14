"""Medición y reporte del análisis de historial. Todo son conteos y montos: nunca identificadores ni datos de clientes."""
import csv
import json
import math
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .. import config
from .campos import CAMPOS
from .reproducir import Resultado

ALERTA = ("EN_REVISION", "RECHAZADA")


def wilson(exitos: int, n: int, z: float = 1.96):
    """Intervalo de confianza del 95 % para una proporción (sirve con pocos casos, donde la fórmula simple falla)."""
    if n == 0:
        return None
    p = exitos / n
    d = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / d
    mitad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centro - mitad), 4), round(min(1.0, centro + mitad), 4)]


def _escenario(res: List[Resultado], es_alerta, costo_falsa_alarma: float) -> Dict:
    tp = sum(1 for r in res if r.fraude and es_alerta(r)); fn = sum(1 for r in res if r.fraude and not es_alerta(r))
    fp = sum(1 for r in res if r.fraude is False and es_alerta(r)); tn = sum(1 for r in res if r.fraude is False and not es_alerta(r))
    sin_modelo = float(sum(r.monto for r in res if r.fraude))
    se_escapa = float(sum(r.monto for r in res if r.fraude and not es_alerta(r)))
    con_sistema = se_escapa + fp * costo_falsa_alarma
    return {"fraudes_detectados": tp, "fraudes_que_escapan": fn, "falsas_alarmas": fp, "legitimos_aprobados": tn,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None, "recall_ic95": wilson(tp, tp + fn),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None, "precision_ic95": wilson(tp, tp + fp),
            "falsas_alarmas_por_1000_pagos": round(1000 * fp / len(res), 1) if res else None,
            "costo_sin_sistema_cop": sin_modelo, "costo_con_sistema_cop": con_sistema,
            "ahorro_cop": sin_modelo - con_sistema, "ahorro_pct": round(100 * (sin_modelo - con_sistema) / sin_modelo, 1) if sin_modelo else None}


def medir(resultados: List[Resultado], filas_leidas: int, motivos: Counter, avisos_campos: Counter, sin_traducir: Dict, solicitudes: List[Dict],
          mapeo: Dict, costo_falsa_alarma: float, madurez_dias: int, modelo: Dict, proveedor_ip: str, seudonimizado: bool, seleccion: Optional[Dict] = None) -> Dict:
    ev = [r for r in resultados if r.decision is not None]
    con_error = Counter(r.error for r in resultados if r.error)
    informe: Dict = {"version_informe": 1, "datos": {"filas_leidas": filas_leidas, "filas_evaluadas": len(ev), "filas_rechazadas_al_leer": sum(motivos.values()),
                     "motivos_de_rechazo": dict(motivos.most_common()), "errores_del_servicio": dict(con_error),
                     "valores_sin_traducir": {k: dict(v) for k, v in sin_traducir.items()}, "campos_descartados_por_valor_invalido": dict(avisos_campos),
                     "ids_seudonimizados": seudonimizado,
                     "desde": min((r.fecha for r in ev), default=None) and min(r.fecha for r in ev).date().isoformat(),
                     "hasta": max((r.fecha for r in ev), default=None) and max(r.fecha for r in ev).date().isoformat()},
                     "modelo": {"nombre": modelo.get("modelo"), "version": modelo.get("version"), "conjunto": modelo.get("conjunto"), "sin_estado_del_dispositivo": bool(modelo.get("sin_estado")),
                                "grupos_de_senales_usados": (seleccion or {}).get("grupos", []), "grupos_no_usados": (seleccion or {}).get("no_usados", {}),
                                "modelos_descartados": (seleccion or {}).get("descartados", [])},
                     "supuestos": {"costo_falsa_alarma_cop": costo_falsa_alarma, "madurez_dias": madurez_dias, "proveedor_ip": proveedor_ip}}
    d = informe["datos"]
    # decisiones
    por_dec = Counter(r.decision for r in ev)
    informe["decisiones"] = {k: {"pagos": por_dec.get(k, 0), "pct": round(100 * por_dec.get(k, 0) / len(ev), 1) if ev else None,
                                 "fraudes": sum(1 for r in ev if r.decision == k and r.fraude)} for k in ("APROBADA", "EN_REVISION", "RECHAZADA")}
    informe["reglas_mas_activadas"] = [{"regla": k, "veces": v} for k, v in Counter(x for r in ev for x in r.reglas).most_common(10)]
    # calidad de los datos
    n = len(ev) or 1
    informe["calidad_de_datos"] = {
        "pct_modo_degradado": round(100 * sum(1 for r in ev if r.modo == "degradado") / n, 1),
        "pct_sin_estado_del_dispositivo": round(100 * sum(1 for r in ev if r.estado_origen == "no_enviado") / n, 1),
        "pct_ip_ubicada_por_el_proveedor": round(100 * sum(1 for r in ev if (r.red_origen or "").startswith("proveedor:")) / n, 1),
        "cobertura_por_campo_pct": {c.ruta: round(100 * sum(1 for s in solicitudes if _tiene(s, c.ruta)) / (len(solicitudes) or 1), 1) for c in CAMPOS}}
    # medición contra los resultados reales (solo pagos con resultado y con tiempo de madurar)
    adv: List[str] = []
    informe["medicion"] = None
    if mapeo["resultado"].get("columna") and ev:
        corte = max(r.fecha for r in ev) - timedelta(days=madurez_dias)
        con_etiqueta = [r for r in ev if r.fraude is not None]
        maduros = [r for r in con_etiqueta if r.fecha <= corte]
        fr = sum(1 for r in maduros if r.fraude)
        informe["medicion"] = {"pagos_con_resultado": len(con_etiqueta), "pagos_maduros_medidos": len(maduros), "fraudes_en_la_medicion": fr, "corte_de_madurez": corte.date().isoformat(),
                               "revisar_o_rechazar": _escenario(maduros, lambda r: r.decision in ALERTA, costo_falsa_alarma),
                               "solo_rechazar": _escenario(maduros, lambda r: r.decision == "RECHAZADA", costo_falsa_alarma)}
        if fr < 30:
            adv.append(f"Hay solo {fr} fraudes en la medición: con tan pocos casos el margen de error es muy grande (ver los intervalos). No saques conclusiones firmes.")
        if len(con_etiqueta) - len(maduros):
            adv.append(f"{len(con_etiqueta) - len(maduros)} pagos son de los últimos {madurez_dias} días y no se midieron: los contracargos llegan semanas o meses después y el resultado de esos pagos todavía no es definitivo.")
        if len(ev) - len(con_etiqueta):
            adv.append(f"{len(ev) - len(con_etiqueta)} pagos no tienen un resultado reconocible (ni fraude ni legítimo) y no entran en la medición.")
    else:
        adv.append("No hay columna de resultado: se evaluó cada pago, pero no se puede medir cuánto fraude se habría detectado.")
    if proveedor_ip == "simulado":
        adv.append("El proveedor de IP es SIMULADO: solo ubica las IP de los rangos de prueba. Con IP reales casi toda la red queda «desconocida» y el resultado es más pobre de lo que sería con un proveedor real, salvo que el archivo ya traiga tipo de IP, país y ciudad.")
    nu = informe["modelo"]["grupos_no_usados"]
    if nu:
        adv.append("El modelo elegido no usa estas señales: " + "; ".join(f"{g} (falta: {', '.join(f)})" if f else f"{g} (este nivel del modelo no la incluye)" for g, f in nu.items()) + ". "
                   "Con esos datos el sistema detectaría más. Pídele al comercio, si puede, esos campos.")
    if d["filas_leidas"] and d["filas_rechazadas_al_leer"] / d["filas_leidas"] > 0.05:
        adv.append(f"Se rechazó el {round(100 * d['filas_rechazadas_al_leer'] / d['filas_leidas'], 1)} % de las filas al leer el archivo (ver motivos): revisa el mapeo.")
    fijos = [r for r in mapeo["por_defecto"] if r in {c.ruta for c in CAMPOS if c.requerido}]
    if fijos:
        adv.append("Estos campos usan un valor fijo para todo el archivo, no el dato real de cada pago: " + ", ".join(fijos) + ".")
    if informe["calidad_de_datos"]["pct_modo_degradado"] > 5:
        adv.append("Más del 5 % de los pagos quedó en modo degradado (faltan datos que el modelo necesita).")
    informe["advertencias"] = adv
    return informe


def cobertura(solicitudes: List[Dict]) -> Dict[str, float]:
    """Qué parte de las filas trae cada campo (0 a 1)."""
    n = len(solicitudes) or 1
    return {c.ruta: sum(1 for x in solicitudes if _tiene(x, c.ruta)) / n for c in CAMPOS}


def _tiene(s: Dict, ruta: str) -> bool:
    if "." in ruta:
        g, k = ruta.split(".", 1)
        return (s.get(g) or {}).get(k) is not None
    return s.get(ruta) is not None


def _m(x) -> str:
    return "—" if x is None else f"$ {x:,.0f}".replace(",", ".")


def _p(x) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def _n(x) -> str:
    return f"{x:,.0f}".replace(",", ".")


def _d(x, dec: int = 1) -> str:
    return f"{x:.{dec}f}".replace(".", ",")


def _ahorro(pct) -> str:
    if pct is None:
        return "sin ahorro medible"
    return f"un ahorro de {_d(pct)} %" if pct >= 0 else f"un sobrecosto de {_d(-pct)} %: con estos supuestos el sistema no compensa"


def _ic(x) -> str:
    return "" if not x else f", con un margen de error entre {100 * x[0]:.0f} % y {100 * x[1]:.0f} %"


def escribir(carpeta: Path, resultados: List[Resultado], informe: Dict) -> Dict[str, Path]:
    carpeta = Path(carpeta); carpeta.mkdir(parents=True, exist_ok=True)
    rutas = {"csv": carpeta / "resultados.csv", "json": carpeta / "informe.json", "md": carpeta / "informe.md"}
    with open(rutas["csv"], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fila_en_el_archivo", "fecha", "monto_cop", "resultado_real", "decision", "puntaje_riesgo", "modo", "reglas", "origen_de_la_red", "estado_del_dispositivo", "error"])
        for r in sorted(resultados, key=lambda x: x.numero):
            w.writerow([r.numero, r.fecha.isoformat(sep=" "), r.monto, "" if r.fraude is None else ("fraude" if r.fraude else "legitimo"), r.decision or "",
                        "" if r.puntaje is None else round(r.puntaje, 4), r.modo or "", " | ".join(r.reglas), r.red_origen or "", r.estado_origen or "", r.error or ""])
    rutas["json"].write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    rutas["md"].write_text(texto(informe), encoding="utf-8")
    return rutas


def texto(i: Dict) -> str:
    d, m, s = i["datos"], i["medicion"], i["supuestos"]
    NOMBRES = {"APROBADA": "Aprobada", "EN_REVISION": "En revisión", "RECHAZADA": "Rechazada"}
    L = ["# Diagnóstico de fraude con tu historial", "",
         f"Se evaluaron {_n(d['filas_evaluadas'])} pagos" + (f" entre {d['desde']} y {d['hasta']}" if d["desde"] else "") + f", con el modelo {i['modelo']['nombre']} versión {i['modelo']['version']}. "
         "Cada pago se reprodujo en orden de fecha, usando solo lo que se sabía hasta ese momento. Nada se guardó fuera de una carpeta temporal que ya se borró.", "",
         f"Nivel del modelo: «{i['modelo']['conjunto']}». Señales que usó: " + (", ".join(i['modelo']['grupos_de_senales_usados']) or "solo las básicas") + ".", ""]
    if m and m["fraudes_en_la_medicion"]:
        a, b = m["revisar_o_rechazar"], m["solo_rechazar"]
        L += ["## Qué habría pasado", "",
              f"De {_n(m['pagos_maduros_medidos'])} pagos con resultado ya definitivo, {m['fraudes_en_la_medicion']} fueron fraude.", "",
              f"- **Si el comercio hubiera revisado o rechazado todo lo que el sistema marca** (EN_REVISION o RECHAZADA): habría detectado {a['fraudes_detectados']} de {m['fraudes_en_la_medicion']} fraudes ({_p(a['recall'])}{_ic(a['recall_ic95'])}). "
              f"Habría generado {_n(a['falsas_alarmas'])} falsas alarmas, unas {_d(a['falsas_alarmas_por_1000_pagos'], 0)} por cada 1.000 pagos. "
              f"El costo habría pasado de {_m(a['costo_sin_sistema_cop'])} a {_m(a['costo_con_sistema_cop'])}: " + _ahorro(a['ahorro_pct']) + ".",
              f"- **Si solo se hubiera rechazado lo más claro** (RECHAZADA): habría detectado {b['fraudes_detectados']} fraudes ({_p(b['recall'])}{_ic(b['recall_ic95'])}), con {_n(b['falsas_alarmas'])} falsas alarmas. Costo: {_m(b['costo_con_sistema_cop'])}: " + _ahorro(b['ahorro_pct']) + ".", "",
              f"Cálculo del costo: lo que se pierde por cada fraude que se escapa, más {_m(s['costo_falsa_alarma_cop'])} por cada falsa alarma. Supone que todo pago que el sistema marca como dudoso o rechazado se frena. "
              "Si revisar un pago le cuesta algo distinto, se puede recalcular.", ""]
    elif m:
        L += ["## Qué habría pasado", "", "No hay fraudes con resultado definitivo en el periodo medido, así que no se puede calcular cuánto habría detectado el sistema.", ""]
    L += ["## Cómo se repartieron las decisiones", ""] + [f"- {NOMBRES[k]}: {_n(v['pagos'])} pagos ({_d(v['pct']) if v['pct'] is not None else '—'} %)" + (f", de los cuales {v['fraudes']} fueron fraude" if m else "") for k, v in i["decisiones"].items()] + [""]
    if i["reglas_mas_activadas"]:
        L += ["## Reglas que más se activaron", ""] + [f"- {r['regla']}: {r['veces']} veces" for r in i["reglas_mas_activadas"]] + [""]
    q = i["calidad_de_datos"]
    L += ["## Calidad de los datos", "", f"- Pagos en modo degradado (faltaban datos): {_d(q['pct_modo_degradado'])} %", f"- Pagos sin el estado del dispositivo: {_d(q['pct_sin_estado_del_dispositivo'])} %",
          f"- Pagos cuya IP ubicó el proveedor: {_d(q['pct_ip_ubicada_por_el_proveedor'])} %", "",
          "Datos que el archivo traía (porcentaje de pagos que tenían cada campo):", ""] + [f"- {k}: {_d(v)} %" for k, v in q["cobertura_por_campo_pct"].items() if v < 100] + [""]
    if d["motivos_de_rechazo"]:
        L += ["## Filas que no se pudieron usar", ""] + [f"- {k}: {v}" for k, v in d["motivos_de_rechazo"].items()] + [""]
    if d["valores_sin_traducir"]:
        L += ["## Textos del archivo que no se supo traducir", "", "Agrégalos a «valores» en el mapeo para no perder esas filas:", ""] + [f"- {c}: " + ", ".join(f"«{k}» ({v})" for k, v in vals.items()) for c, vals in d["valores_sin_traducir"].items()] + [""]
    if i["advertencias"]:
        L += ["## Cosas que debes tener en cuenta", ""] + [f"- {a}" for a in i["advertencias"]] + [""]
    L += ["## Sobre la privacidad", "", "Los códigos de cliente, de pedido y de dispositivo se convirtieron en códigos irreversibles antes de evaluar, y las IP no se guardan: solo una huella que no se puede revertir. " if d["ids_seudonimizados"] else "Atención: esta ejecución mantuvo los identificadores originales. ",
          "Los resultados por pago (resultados.csv) se identifican por el número de fila del archivo original.", ""]
    return "\n".join(L)
