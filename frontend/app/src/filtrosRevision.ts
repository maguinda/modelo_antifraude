import type { Parametros } from "./red/cliente";

// Los filtros de la cola de revisión. Viven en la dirección (`?estado=resuelta&riesgo=alto`): se pueden copiar y «atrás» funciona.
// Lo que llegue de la dirección se SANEA aquí: un valor raro se descarta, nunca produce un error ni llega al servidor.

export const ORDENES_REVISION = [
  ["antiguos", "Los que más esperan"],
  ["recientes", "Más recientes"],
  ["riesgo_desc", "Mayor riesgo"],
  ["monto_desc", "Mayor monto"],
] as const;
export type OrdenRevision = (typeof ORDENES_REVISION)[number][0];
export type Riesgo = "" | "alto" | "medio" | "bajo";

export interface FiltrosRevision {
  estado: "pendiente" | "resuelta";
  orden: OrdenRevision;
  q: string;
  comercio: string;
  montoMin: string;
  desde: string;
  hasta: string;
  riesgo: Riesgo;
}

export const FILTROS_INICIALES: FiltrosRevision = {
  estado: "pendiente",
  orden: "antiguos",
  q: "",
  comercio: "",
  montoMin: "",
  desde: "",
  hasta: "",
  riesgo: "",
};

const FECHA = /^\d{4}-\d{2}-\d{2}$/;

export function montoValido(v: string): boolean {
  return v !== "" && Number.isFinite(Number(v)) && Number(v) >= 0;
}

export function leerFiltros(p: URLSearchParams): FiltrosRevision {
  const orden = ORDENES_REVISION.find(([v]) => v === p.get("orden"))?.[0] ?? "antiguos";
  const riesgo = (["alto", "medio", "bajo"] as const).find((v) => v === p.get("riesgo")) ?? "";
  const monto = (p.get("monto_min") ?? "").trim();
  const desde = p.get("desde") ?? "";
  const hasta = p.get("hasta") ?? "";
  return {
    estado: p.get("estado") === "resuelta" ? "resuelta" : "pendiente",
    orden,
    q: (p.get("q") ?? "").trim().slice(0, 64),
    comercio: /^\d{1,9}$/.test(p.get("comercio_id") ?? "") ? (p.get("comercio_id") as string) : "",
    montoMin: montoValido(monto) ? monto : "",
    desde: FECHA.test(desde) ? desde : "",
    hasta: FECHA.test(hasta) ? hasta : "",
    riesgo,
  };
}

/** Los mensajes de validación del formulario (los mismos criterios que el servidor: monto ≥ 0, «Desde» no posterior a «Hasta»). */
export function validarFiltros(f: Pick<FiltrosRevision, "montoMin" | "desde" | "hasta">): { monto: string; fechas: string } {
  return {
    monto: f.montoMin !== "" && !montoValido(f.montoMin) ? "Escribe un número igual o mayor que 0." : "",
    fechas: f.desde && f.hasta && f.desde > f.hasta ? "«Desde» no puede ser posterior a «Hasta»." : "",
  };
}

/** Lo que se le pide al servidor. Un rango de fechas al revés (por un enlace viejo) se ignora en vez de provocar un error. */
export function aParametrosApi(f: FiltrosRevision, limite: number, desplazamiento: number): Parametros {
  const p: Parametros = { estado: f.estado, limite, desplazamiento, orden: f.orden };
  if (f.q) p.q = f.q;
  if (f.comercio) p.comercio_id = f.comercio;
  if (f.montoMin !== "") p.monto_min = f.montoMin;
  if (!(f.desde && f.hasta && f.desde > f.hasta)) {
    if (f.desde) p.desde = f.desde;
    if (f.hasta) p.hasta = f.hasta;
  }
  if (f.riesgo === "alto") p.riesgo_min = 0.6;
  else if (f.riesgo === "medio") {
    p.riesgo_min = 0.35;
    p.riesgo_max = 0.5999;
  } else if (f.riesgo === "bajo") p.riesgo_max = 0.3499;
  return p;
}

/** Para la dirección: solo lo que difiere de lo inicial (null borra el parámetro). */
export function filtrosAUrl(f: FiltrosRevision): Record<string, string | null> {
  const o = (v: string, inicial = "") => (v === inicial ? null : v);
  return {
    estado: o(f.estado, "pendiente"),
    orden: o(f.orden, "antiguos"),
    q: o(f.q),
    comercio_id: o(f.comercio),
    monto_min: o(f.montoMin),
    desde: o(f.desde),
    hasta: o(f.hasta),
    riesgo: o(f.riesgo),
    pagina: null,
  };
}

/** ¿Hay algún filtro puesto? (estado y orden no cuentan: son «cómo ver», no «qué ver»). */
export function hayFiltros(f: FiltrosRevision): boolean {
  return Boolean(f.q || f.comercio || f.montoMin !== "" || f.desde || f.hasta || f.riesgo);
}
