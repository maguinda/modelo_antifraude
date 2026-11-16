import type { PaginaTransacciones, Transaccion } from "../red/tipos";

export function respuesta(estado: number, cuerpo: unknown): Response {
  return new Response(JSON.stringify(cuerpo), { status: estado, headers: { "Content-Type": "application/json" } });
}

export function transaccion(n: number): Transaccion {
  return {
    id_evaluacion: `ev${n}`,
    id_seguimiento: `AF-${String(n).padStart(4, "0")}`,
    comercio: "Tienda",
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
