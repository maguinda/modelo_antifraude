import { useEffect, useMemo } from "react";
import { ErrorApi, type Cliente } from "../red/cliente";
import type { OrdenTransacciones, PaginaTransacciones } from "../red/tipos";
import { Paginacion } from "../componentes/Paginacion";
import { useConsulta } from "../hooks/useConsulta";
import { useParametrosUrl } from "../hooks/useParametrosUrl";
import { fecha, pesos, DECISIONES } from "../formato";
import { ajustarPagina, hayMasAllaDelTope, tamanoValido } from "../paginacion";
import estilos from "./Transacciones.module.css";

const ORDENES: ReadonlyArray<[OrdenTransacciones, string]> = [
  ["fecha_desc", "Más recientes"],
  ["fecha_asc", "Más antiguos"],
  ["riesgo_desc", "Mayor riesgo"],
  ["monto_desc", "Mayor monto"],
];
const FILAS_ESPERA = 5;

interface Props {
  cliente: Cliente;
  /** La clave ya no sirve (401/403): se vuelve a pedir. */
  alExpirar: () => void;
}

export function Transacciones({ cliente, alExpirar }: Props) {
  const [params, cambiar] = useParametrosUrl();
  const porPagina = tamanoValido(params.get("limite"));
  const orden = (ORDENES.find(([v]) => v === params.get("orden"))?.[0] ?? "fecha_desc") as OrdenTransacciones;
  // La página pedida se corrige con el total que ya se conoce (si no hay, se confía hasta que llegue el primero).
  const pedida = ajustarPagina(params.get("pagina"), Number.MAX_SAFE_INTEGER, porPagina); // con tope: nunca pide un desplazamiento que el servidor rechace

  const clave = `${orden}|${porPagina}|${pedida}`;
  const { datos, cargando, error, recargar } = useConsulta<PaginaTransacciones>(clave, (senal) =>
    cliente.obtener<PaginaTransacciones>(
      "/v1/panel/transacciones",
      { orden, limite: porPagina, desplazamiento: (pedida - 1) * porPagina },
      senal,
    ),
  );

  const total = datos?.total ?? 0;
  const pagina = datos ? ajustarPagina(pedida, total, porPagina) : pedida;

  // Si el total bajó y la página pedida quedó más allá del final, se vuelve a la última (sin pantalla de error).
  useEffect(() => {
    if (datos && !cargando && pagina !== pedida) cambiar({ pagina: String(pagina) });
  }, [datos, cargando, pagina, pedida, cambiar]);

  useEffect(() => {
    if (error instanceof ErrorApi && (error.estado === 401 || error.estado === 403)) alExpirar();
  }, [error, alExpirar]);

  const filas = useMemo(() => datos?.transacciones ?? [], [datos]);
  const sinDatosAun = datos === null;

  return (
    <section aria-label="Transacciones" aria-busy={cargando}>
      <div className={estilos.encabezado}>
        <h2>Transacciones</h2>
        <div className={`campo ${estilos.campoOrden}`}>
          <label htmlFor="orden">Ordenar por</label>
          <select id="orden" value={orden} disabled={cargando} onChange={(e) => cambiar({ orden: e.target.value, pagina: null })}>
            {ORDENES.map(([v, t]) => (
              <option key={v} value={v}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && !(error instanceof ErrorApi && (error.estado === 401 || error.estado === 403)) ? (
        <div className={estilos.aviso} role="alert">
          <span>No se pudo cargar: {error.message}</span>
          <button type="button" className="btn suave" onClick={recargar}>
            Reintentar
          </button>
        </div>
      ) : null}

      <div className={`${estilos.tabla} ${cargando && !sinDatosAun ? estilos.refrescando : ""}`}>
        <table>
          <thead>
            <tr>
              <th scope="col">Seguimiento</th>
              <th scope="col">Fecha</th>
              <th scope="col">Comercio</th>
              <th scope="col">Cliente</th>
              <th scope="col" className={estilos.num}>
                Monto
              </th>
              <th scope="col">Decisión</th>
            </tr>
          </thead>
          <tbody>
            {sinDatosAun && cargando
              ? Array.from({ length: FILAS_ESPERA }, (_, i) => (
                  <tr key={i} className={estilos.espera} aria-hidden="true">
                    <td colSpan={6}>
                      <span className={estilos.barraEspera} />
                    </td>
                  </tr>
                ))
              : filas.map((t) => {
                  const dec = t.decision_final ?? t.decision;
                  return (
                    <tr key={t.id_evaluacion}>
                      <td data-etiqueta="Seguimiento">{t.id_seguimiento}</td>
                      <td data-etiqueta="Fecha">{fecha(t.fecha)}</td>
                      <td data-etiqueta="Comercio">{t.comercio}</td>
                      <td data-etiqueta="Cliente">{t.id_cliente}</td>
                      <td data-etiqueta="Monto" className={estilos.num}>
                        {pesos(t.monto_cop)}
                      </td>
                      <td data-etiqueta="Decisión">
                        <span className={`${estilos.estado} ${estilos[dec] ?? ""}`}>{DECISIONES[dec] ?? dec}</span>
                        {t.decision_final === null && t.decision === "EN_REVISION" ? (
                          <span className="chip">por decidir</span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
          </tbody>
        </table>
        {!sinDatosAun && filas.length === 0 && !cargando ? <p className={estilos.vacio}>No hay pagos para mostrar.</p> : null}
      </div>

      {!sinDatosAun && hayMasAllaDelTope(total, porPagina) ? (
        <p className={estilos.nota}>
          Hay más pagos de los que se pueden recorrer por páginas. Para ver el resto, afina con filtros (por ejemplo, por fecha).
        </p>
      ) : null}

      {!sinDatosAun ? (
        <Paginacion
          total={total}
          pagina={pagina}
          porPagina={porPagina}
          ocupado={cargando}
          alCambiarPagina={(p) => cambiar({ pagina: p <= 1 ? null : String(p) })}
          alCambiarTamano={(t) => cambiar({ limite: String(t), pagina: null })}
        />
      ) : null}
    </section>
  );
}
