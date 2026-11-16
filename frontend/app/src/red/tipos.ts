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
