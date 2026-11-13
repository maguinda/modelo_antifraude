"""Prueba de carga: muchas peticiones a la vez contra un servidor que YA está corriendo.

    python -m modelo_ia.api.prueba_carga --url http://127.0.0.1:8000 --clave "$CLAVE_DE_COMERCIO" --peticiones 2000 --concurrencia 16

Usa pagos de ejemplo con ids nuevos en cada corrida (cambia --semilla) y un 20 % de reintentos del mismo pago. Úsala SOLO contra una base de demostración o de prueba:
escribe miles de evaluaciones. Revisa que no haya errores 5xx, que los reintentos devuelvan lo mismo y mide velocidad y latencias.
"""
import argparse
import asyncio
import copy
import random
import statistics
import time
from typing import Dict, List

import httpx

from .ejemplos import EJEMPLOS_SOLICITUD

PLANTILLAS = [EJEMPLOS_SOLICITUD[k]["value"] for k in ("01_aprobada_pago_normal", "02_en_revision_comportamiento_poco_humano", "03_en_revision_vpn_cliente_nuevo",
                                                      "04_rechazada_pago_sospechoso", "07_degradado_solo_campos_obligatorios")]


def construir_pago(i: int, rng: random.Random, clientes: int, semilla: int = 0) -> Dict:
    p = copy.deepcopy(rng.choice(PLANTILLAS))
    p["id_transaccion"] = f"CARGA-{semilla}-{i}"            # la semilla va en el id: cada corrida usa pagos nuevos
    p["id_cliente"] = f"CARGA-CLI-{rng.randrange(clientes)}"
    if "huella" in p["dispositivo"]:
        p["dispositivo"]["huella"] = f"FP-carga-{rng.randrange(clientes)}"
    return p


async def correr(url: str, clave: str, peticiones: int, concurrencia: int, reintentos: float, clientes: int, semilla: int) -> Dict:
    rng = random.Random(semilla)
    pagos = [construir_pago(i, rng, clientes, semilla) for i in range(peticiones)]
    trabajos = [(p, False) for p in pagos] + [(p, True) for p in rng.sample(pagos, int(peticiones * reintentos))]
    rng.shuffle(trabajos)
    # un reintento solo tiene sentido DESPUÉS del original: se manda al final
    trabajos.sort(key=lambda t: t[1])
    originales: Dict[str, str] = {}
    latencias: List[float] = []
    codigos: Dict[int, int] = {}
    reintento_distinto = 0
    cola = asyncio.Queue()
    for t in trabajos:
        cola.put_nowait(t)
    inicio = time.perf_counter()

    async def trabajador(cliente: httpx.AsyncClient):
        nonlocal reintento_distinto
        while True:
            try:
                pago, es_reintento = cola.get_nowait()
            except asyncio.QueueEmpty:
                return
            t0 = time.perf_counter()
            try:
                r = await cliente.post("/v1/evaluaciones", json=pago)
                codigo = r.status_code
            except Exception:
                codigo, r = 0, None
            latencias.append((time.perf_counter() - t0) * 1000)
            codigos[codigo] = codigos.get(codigo, 0) + 1
            if r is not None and codigo in (200, 201):
                clave_id = pago["id_transaccion"]
                if es_reintento:
                    # solo cuenta si el original sí se guardó (si falló con un 5xx, el «reintento» es en realidad el primer intento)
                    if clave_id in originales and originales[clave_id] != r.json()["id_evaluacion"]:
                        reintento_distinto += 1
                else:
                    originales[clave_id] = r.json()["id_evaluacion"]

    limites = httpx.Limits(max_connections=concurrencia, max_keepalive_connections=concurrencia)
    async with httpx.AsyncClient(base_url=url, headers={"X-API-Key": clave}, timeout=120, limits=limites, verify=False) as c:
        # los originales primero (en paralelo); los reintentos después, para que siempre exista el original
        n_orig = sum(1 for t in trabajos if not t[1])
        cola_orig, cola_rein = asyncio.Queue(), asyncio.Queue()
        for t in trabajos:
            (cola_rein if t[1] else cola_orig).put_nowait(t)
        for fase in (cola_orig, cola_rein):
            cola = fase
            await asyncio.gather(*[trabajador(c) for _ in range(concurrencia)])
    total = time.perf_counter() - inicio
    ordenadas = sorted(latencias)
    q = lambda p: ordenadas[min(len(ordenadas) - 1, int(len(ordenadas) * p))]
    return {"peticiones": len(latencias), "segundos": round(total, 1), "por_segundo": round(len(latencias) / total, 1), "codigos": codigos,
            "p50_ms": round(statistics.median(latencias), 1), "p95_ms": round(q(0.95), 1), "p99_ms": round(q(0.99), 1), "max_ms": round(max(latencias), 1),
            "originales_distintos": len(originales), "reintentos_que_devolvieron_otra_cosa": reintento_distinto}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Prueba de carga de la API antifraude (solo contra bases de prueba).")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--clave", required=True, help="clave de un COMERCIO de prueba")
    ap.add_argument("--peticiones", type=int, default=2000)
    ap.add_argument("--concurrencia", type=int, default=16)
    ap.add_argument("--reintentos", type=float, default=0.2, help="parte de los pagos que se reenvían")
    ap.add_argument("--clientes", type=int, default=300, help="clientes distintos (menos clientes = historial más largo)")
    ap.add_argument("--semilla", type=int, default=1, help="cambia los ids: con la misma semilla en la misma base, todo serían reintentos")
    a = ap.parse_args(argv)
    r = asyncio.run(correr(a.url, a.clave, a.peticiones, a.concurrencia, a.reintentos, a.clientes, a.semilla))
    for k, v in r.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
