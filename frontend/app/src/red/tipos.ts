// Tipos de las respuestas de la API. NO se escriben a mano: salen del contrato (`api/openapi.json`) en `api.generado.ts`
// (se regenera con `python -m modelo_ia.api.exportar_tipos_ts` y una prueba del repo falla si no está al día).
// Este archivo solo les pone los nombres cortos que usan las pantallas y deja aquí lo que el contrato no puede decir.
import type {
  ComercioPanel,
  Decision as DecisionApi,
  DecisionAnalista as DecisionAnalistaApi,
  FilaRevision,
  ListaRevisiones,
  PaginaTransaccionesPanel,
  ReglaActivada as ReglaActivadaApi,
  RespuestaYo,
  Senal,
  Senales as SenalesApi,
  TransaccionPanel,
} from "./api.generado";

export type Decision = DecisionApi;
export type DecisionAnalista = DecisionAnalistaApi;
export type Transaccion = TransaccionPanel;
export type PaginaTransacciones = PaginaTransaccionesPanel;
export type PesoSenal = Senal;
export type Senales = SenalesApi;
export type ReglaActivada = ReglaActivadaApi;
export type Revision = FilaRevision;
export type PaginaRevisiones = ListaRevisiones;
export type ComercioFiltro = ComercioPanel;
export type Yo = RespuestaYo;

export type OrdenTransacciones = "fecha_desc" | "fecha_asc" | "riesgo_desc" | "monto_desc";
