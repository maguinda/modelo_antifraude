import type { ComercioPanel, RespuestaResumenPanel } from "../red/api.generado";
import type { PaginaTransacciones, Transaccion } from "../red/tipos";

export function respuesta(estado: number, cuerpo: unknown): Response {
  return new Response(JSON.stringify(cuerpo), { status: estado, headers: { "Content-Type": "application/json" } });
}

export function transaccion(n: number): Transaccion {
  return {
    id_evaluacion: `ev${n}`,
    id_seguimiento: `AF-${String(n).padStart(4, "0")}`,
    comercio_id: 2,
    comercio: "Tienda",
    id_transaccion: `TX${n}`,
    id_cliente: `CLI${n}`,
    fecha: "2026-11-01T10:00:00",
    monto_cop: 1000 * n,
    decision: "APROBADA",
    decision_final: "APROBADA",
    puntaje_riesgo: 0.1,
    modo: "normal",
    reglas: [],
  };
}

/** Un servidor falso con `total` pagos que respeta limite y desplazamiento, como el real. */
export function servidorFalso(total: number) {
  const pedidas: URL[] = [];
  const fetcher = (async (entrada: RequestInfo | URL) => {
    const url = new URL(String(entrada), "http://x");
    pedidas.push(url);
    const limite = Number(url.searchParams.get("limite") ?? 50);
    const desde = Number(url.searchParams.get("desplazamiento") ?? 0);
    const items = Array.from({ length: Math.max(0, Math.min(limite, total - desde)) }, (_, i) => transaccion(desde + i + 1));
    const cuerpo: PaginaTransacciones = { total, transacciones: items };
    return respuesta(200, cuerpo);
  }) as typeof fetch;
  return { fetcher, pedidas };
}

// ---- Cola de revisión ----
import type { Revision } from "../red/tipos";

export function revision(n: number, cambios: Partial<Revision> = {}): Revision {
  return {
    id_evaluacion: `rev${n}`,
    id_seguimiento: `AF-${String(n).padStart(4, "0")}`,
    comercio: "Tienda",
    comercio_id: 2,
    id_transaccion: `TX${n}`,
    puntaje_riesgo: 0.7,
    motivos: [],
    reglas_activadas: [],
    creada_en: "2026-11-01T10:00:00",
    estado: "pendiente",
    resuelta_en: null,
    resuelta_por: null,
    senales: { suben: [], bajan: [], puntaje_tipico: null }, // la API siempre manda el objeto (vacío si no hay señales), nunca null
    resumen_pago: { monto_cop: 1000 * n, id_cliente: `CLI${n}`, metodo_pago: "VISA" },
    ...cambios,
  };
}

/** Un servidor falso de la cola: respeta estado, limite y desplazamiento; resolver marca la revisión, o da 409 si ya estaba resuelta. */
export function servidorDeRevisiones(pendientes: number, extra: Partial<Revision>[] = []) {
  const todas: Revision[] = [...Array.from({ length: pendientes }, (_, i) => revision(i + 1)), ...extra.map((e, i) => revision(1000 + i, e))];
  const llamadas: Array<{ metodo: string; url: URL; cuerpo?: unknown }> = [];
  const fallos: { resolver?: () => Response | Promise<Response>; lista?: () => Response | Promise<Response> } = {};
  const fetcher = (async (entrada: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(entrada), "http://x");
    const metodo = init?.method ?? "GET";
    const cuerpo = init?.body ? JSON.parse(String(init.body)) : undefined;
    llamadas.push({ metodo, url, cuerpo });
    if (url.pathname === "/v1/panel/comercios") return respuesta(200, [{ id: 2, nombre: "Tienda", suspendido: 0 }, { id: 3, nombre: "Otra", suspendido: 0 }]);
    if (url.pathname === "/v1/yo") return respuesta(200, { rol: "analista", usuario: "Ana", comercio: "PLATAFORMA" });
    const m = url.pathname.match(/^\/v1\/revisiones\/([^/]+)\/resolver$/);
    if (m && metodo === "POST") {
      if (fallos.resolver) return fallos.resolver();
      const rev = todas.find((r) => r.id_evaluacion === decodeURIComponent(m[1] as string));
      if (!rev) return respuesta(404, { detail: "no existe" });
      if (rev.estado === "resuelta") return respuesta(409, { detail: "ya resuelta" });
      rev.estado = "resuelta";
      rev.resuelta_por = "Ana";
      return respuesta(200, { id_evaluacion: rev.id_evaluacion });
    }
    if (url.pathname === "/v1/revisiones") {
      if (fallos.lista) return fallos.lista();
      const estado = url.searchParams.get("estado") ?? "pendiente";
      const q = url.searchParams.get("q");
      const delEstado = todas.filter((r) => r.estado === estado && (!q || r.id_transaccion.includes(q)));
      const limite = Number(url.searchParams.get("limite") ?? 50);
      const desde = Number(url.searchParams.get("desplazamiento") ?? 0);
      return respuesta(200, {
        total: delEstado.length,
        monto_total_cop: delEstado.reduce((s, r) => s + Number(r.resumen_pago?.monto_cop ?? 0), 0),
        revisiones: delEstado.slice(desde, desde + limite),
      });
    }
    return respuesta(404, { detail: "ruta de prueba no prevista" });
  }) as typeof fetch;
  return { fetcher, llamadas, todas, fallos };
}

/** Un resumen completo y creíble; `cambios` pisa solo lo de primer nivel. */
export function resumenEjemplo(cambios: Partial<RespuestaResumenPanel> = {}): RespuestaResumenPanel {
  return {
    totales: {
      pagos: 200,
      aprobadas: 150,
      en_revision: 30,
      rechazadas: 20,
      monto_cop: 20_000_000,
      monto_rechazado_cop: 2_500_000,
      monto_en_revision_cop: 3_000_000,
      modo_degradado: 10,
      sin_estado_del_dispositivo: 4,
      revisiones_pendientes: 7,
      latencia_media_ms: 19.46,
      latencia_p95_ms: 31.2,
    },
    por_dia: [
      { dia: "2026-11-01", aprobadas: 70, en_revision: 10, rechazadas: 5 },
      { dia: "2026-11-02", aprobadas: 80, en_revision: 20, rechazadas: 15 },
    ],
    por_comercio: [
      { comercio_id: 2, nombre: "Tienda A", pagos: 120, aprobadas: 90, en_revision: 20, rechazadas: 10, monto_rechazado: 1_000_000 },
      { comercio_id: 3, nombre: "Tienda B", pagos: 80, aprobadas: 60, en_revision: 10, rechazadas: 10, monto_rechazado: 1_500_000 },
    ],
    reglas_mas_activadas: [
      { regla: "monto_alto", veces: 12, rechazadas: 4 },
      { regla: "ip_anonima", veces: 5, rechazadas: 2 },
    ],
    por_analista: [{ analista: "Ana", resueltas: 9, aprobadas: 6, rechazadas: 3, minutos_promedio: 12.5 }],
    distribucion_del_puntaje: Array.from({ length: 10 }, (_, i) => ({ desde: i / 10, hasta: (i + 1) / 10, pagos: i === 0 ? 150 : i * 2 })),
    ...cambios,
  };
}

/** Un servidor falso del resumen: guarda cada pedido y puede fallar o cambiar su respuesta. */
export function servidorDeResumen(inicial: RespuestaResumenPanel = resumenEjemplo(), comercios: ComercioPanel[] = [
  { id: 2, nombre: "Tienda A", suspendido: 0, pagos: 120 },
  { id: 3, nombre: "Tienda B", suspendido: 0, pagos: 80 },
]) {
  const pedidas: URL[] = [];
  const estado: { resumen: RespuestaResumenPanel; fallo?: () => Response } = { resumen: inicial };
  const fetcher = (async (entrada: RequestInfo | URL) => {
    const url = new URL(String(entrada), "http://x");
    if (url.pathname === "/v1/panel/comercios") return respuesta(200, comercios);
    if (url.pathname === "/v1/panel/resumen") {
      pedidas.push(url);
      return estado.fallo ? estado.fallo() : respuesta(200, estado.resumen);
    }
    return respuesta(404, { detail: "ruta de prueba no prevista" });
  }) as typeof fetch;
  return { fetcher, pedidas, estado };
}
