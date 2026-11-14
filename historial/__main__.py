"""Línea de comandos del adaptador de historial.

  python -m modelo_ia.historial ejemplo   --salida carpeta                 crea un archivo de pagos INVENTADO y su mapeo para probar
  python -m modelo_ia.historial plantilla --salida mapeo.json              crea un mapeo vacío con todos los campos
  python -m modelo_ia.historial sugerir   archivo.csv --salida mapeo.json  propone un mapeo mirando los nombres de las columnas
  python -m modelo_ia.historial verificar archivo.csv --mapeo mapeo.json   revisa el mapeo y cuántas filas se podrían usar (no evalúa)
  python -m modelo_ia.historial evaluar   archivo.csv --mapeo mapeo.json --salida carpeta   reproduce el historial y mide

Solo imprime conteos y nombres de campo: nunca valores de clientes.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from . import ejemplo as ej
from . import informe as inf
from . import lectura, mapeo as mp, seleccion
from .conversion import Convertidor
from .reproducir import reproducir


def _convertir(archivo: Path, m: dict, seudonimizar: bool, limite=None):
    conv = Convertidor(m, seudonimizar)
    filas, leidas = [], 0
    for numero, fila in lectura.leer(archivo, m["formato"]):
        leidas += 1
        filas.append(conv.convertir(numero, fila))
        if limite and leidas >= limite:
            break
    return conv, filas, leidas


def _resumen_lectura(filas, leidas) -> Counter:
    motivos = Counter(f.motivo for f in filas if f.motivo)
    print(f"Filas leídas: {leidas:,} | usables: {sum(1 for f in filas if f.solicitud):,} | rechazadas: {sum(motivos.values()):,}".replace(",", "."))
    for k, v in motivos.most_common(8):
        print(f"   - {k}: {v}")
    return motivos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modelo_ia.historial", description="Diagnóstico de fraude con el historial de pagos de un comercio.")
    sub = ap.add_subparsers(dest="orden", required=True)
    e = sub.add_parser("ejemplo"); e.add_argument("--salida", type=Path, required=True); e.add_argument("--n", type=int, default=3000)
    t = sub.add_parser("plantilla"); t.add_argument("--salida", type=Path, required=True)
    s = sub.add_parser("sugerir"); s.add_argument("archivo", type=Path); s.add_argument("--salida", type=Path, required=True); s.add_argument("--separador", default="auto")
    v = sub.add_parser("verificar"); v.add_argument("archivo", type=Path); v.add_argument("--mapeo", type=Path, required=True)
    ev = sub.add_parser("evaluar"); ev.add_argument("archivo", type=Path); ev.add_argument("--mapeo", type=Path, required=True); ev.add_argument("--salida", type=Path, required=True)
    ev.add_argument("--costo-falsa-alarma", type=float, default=15000.0, help="lo que cuesta revisar o perder un pago legítimo (COP)")
    ev.add_argument("--madurez-dias", type=int, default=90, help="los pagos más recientes que esto no se miden: sus contracargos aún pueden llegar")
    ev.add_argument("--limite", type=int, default=None, help="solo las primeras N filas (para probar)")
    ev.add_argument("--sin-seudonimos", action="store_true", help="NO convertir los ids en códigos irreversibles (solo para depurar con datos tuyos)")
    ev.add_argument("--proveedor-ip", default="simulado")
    ev.add_argument("--nivel", default="auto", choices=["auto", "completo", "perfil", "historial"], help="qué modelo usar; «auto» elige el más completo que el archivo pueda alimentar")
    a = ap.parse_args(argv)
    try:
        if a.orden == "ejemplo":
            r = ej.generar(a.salida, a.n)
            print(f"Archivo inventado: {r['archivo']}\nMapeo listo:      {r['mapeo']}\nSiguiente paso:   python -m modelo_ia.historial evaluar {r['archivo']} --mapeo {r['mapeo']} --salida {a.salida / 'resultado'}")
            return 0
        if a.orden == "plantilla":
            mp.guardar(mp.plantilla(), a.salida); print(f"Plantilla escrita en {a.salida}"); return 0
        if a.orden == "sugerir":
            cols = lectura.encabezados(a.archivo, {"separador": a.separador})
            m = mp.sugerir(cols); m["formato"]["separador"] = a.separador; mp.guardar(m, a.salida)
            puestas = {k: c for k, c in m["columnas"].items() if c}
            print(f"Columnas del archivo: {len(cols)} | campos propuestos: {len(puestas)}")
            for k, c in puestas.items():
                print(f"   {k}  ←  {c}")
            print(f"Mapeo escrito en {a.salida}. REVÍSALO: completa lo que falte y la sección «valores» (cómo se llaman en tu archivo los métodos de pago, categorías, etc.).")
            return 0
        m = mp.cargar(a.mapeo)
        cols = lectura.encabezados(a.archivo, m["formato"])
        chequeo = mp.verificar(m, cols)
        for x in chequeo["errores"]:
            print("ERROR:", x)
        for x in chequeo["avisos"]:
            print("Aviso:", x)
        if chequeo["errores"]:
            return 2
        if a.orden == "verificar":
            conv, filas, leidas = _convertir(a.archivo, m, True, 5000)
            _resumen_lectura(filas, leidas)
            for c, vals in conv.valores_sin_traducir.items():
                print(f"   Sin traducir en {c}: " + ", ".join(f"«{k}» ({n})" for k, n in vals.items()))
            return 0
        conv, filas, leidas = _convertir(a.archivo, m, not a.sin_seudonimos, a.limite)
        motivos = _resumen_lectura(filas, leidas)
        if not any(f.solicitud for f in filas):
            print("Ninguna fila se pudo usar. Corre «verificar» para ver por qué."); return 2
        sol = [f.solicitud for f in filas if f.solicitud]
        try:
            sel = seleccion.elegir(inf.cobertura(sol), a.nivel)
        except ValueError as err:
            print("ERROR:", err); return 2
        cand = sel["candidato"]
        print(f"Modelo elegido: nivel «{cand['conjunto']}»" + (" (sin estado del dispositivo)" if cand["sin_estado"] else "") + f", señales que usa: {', '.join(cand['grupos']) or 'solo las básicas'}.")
        for d in sel["descartados"]:
            print(f"   Descartado {d['modelo']}: le faltan {', '.join(d['faltan'])}")
        art = cand["artefacto"]
        print("Reproduciendo el historial en orden de fecha (puede tardar: unos 60 pagos por segundo con historiales grandes)…")
        rep = reproducir(filas, art, art if cand["sin_estado"] else None, proveedor=a.proveedor_ip, avance=lambda i, n: print(f"   {i:,}/{n:,}".replace(",", "."), flush=True))
        avisos = Counter(x for f in filas for x in f.avisos)
        rep_inf = inf.medir(rep["resultados"], leidas, motivos, avisos, conv.valores_sin_traducir, sol, m, a.costo_falsa_alarma, a.madurez_dias, art["meta"], rep["proveedor_ip"],
                            not a.sin_seudonimos, {"grupos": cand["grupos"], "no_usados": sel["no_usados"], "descartados": sel["descartados"]})
        rutas = inf.escribir(a.salida, rep["resultados"], rep_inf)
        print(f"Listo. Informe: {rutas['md']}\n       Detalle por pago: {rutas['csv']}")
        return 0
    except (lectura.ErrorDeArchivo, mp.ErrorDeMapeo) as err:
        print("ERROR:", err); return 2


if __name__ == "__main__":
    sys.exit(main())
