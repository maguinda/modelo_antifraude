// Forma de las respuestas del panel. El OpenAPI actual describe estas rutas como «objeto libre», así que se escriben a mano
// (pendiente en el backend: modelos de respuesta; entonces estos tipos se generan). Una prueba del repo vigila que las rutas existan.

export type Decision = "APROBADA" | "EN_REVISION" | "RECHAZADA";

export interface Transaccion {
  id_evaluacion: string;
  id_seguimiento: string;
  comercio: string;
  id_cliente: string;
  fecha: string;
  monto_cop: number | null;
  decision: Decision;
  decision_final: Decision | null;
  puntaje_riesgo: number | null;
  modo: string;
  reglas: string[];
}

export interface PaginaTransacciones {
  total: number;
  transacciones: Transaccion[];
}

export type OrdenTransacciones = "fecha_desc" | "fecha_asc" | "riesgo_desc" | "monto_desc";

// ---- Cola de revisión (GET /v1/revisiones, POST /v1/revisiones/{id}/resolver) ----

export type DecisionAnalista = "APROBADA" | "RECHAZADA";

export interface PesoSenal {
  clave: string;
  texto: string;
  puntos: number;
}

export interface Senales {
  suben?: PesoSenal[];
  bajan?: PesoSenal[];
  puntaje_tipico?: number | null;
}

export interface ReglaActivada {
  regla: string;
  motivo: string;
  accion: string;
}

export interface Revision {
  id_evaluacion: string;
  id_seguimiento: string | null;
  comercio: string | null;
  comercio_id: number;
  id_transaccion: string;
  puntaje_riesgo: number | null;
  motivos: string[];
  reglas_activadas: ReglaActivada[];
  creada_en: string;
  estado: "pendiente" | "resuelta";
  resuelta_en: string | null;
  resuelta_por: string | null;
  senales: Senales | null;
  resumen_pago: Record<string, unknown>;
}

export interface PaginaRevisiones {
  total: number;
  monto_total_cop: number;
  revisiones: Revision[];
}

export interface ComercioFiltro {
  id: number;
  nombre: string;
  suspendido: number;
}

export interface Yo {
  rol: string;
  usuario: string | null;
  comercio: string;
}
