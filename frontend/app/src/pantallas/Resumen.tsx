import { useEffect, useMemo } from "react";
import { ErrorApi, type Cliente } from "../red/cliente";
import type { RespuestaResumenPanel } from "../red/api.generado";
import type { ComercioFiltro } from "../red/tipos";
import { FiltrosResumenForm } from "../componentes/FiltrosResumenForm";
import { GraficaPorDia } from "../componentes/GraficaPorDia";
import { GraficaPuntaje } from "../componentes/GraficaPuntaje";
import { ListaBarras } from "../componentes/ListaBarras";
import { useConsulta } from "../hooks/useConsulta";
import { useParametrosUrl } from "../hooks/useParametrosUrl";
import { numero, pesos, pesosCompactos } from "../formato";
import { aParametrosResumen, conUnidad, filtrosResumenAUrl, leerFiltrosResumen, porcentaje, type FiltrosResumen } from "../resumen";
import estilos from "./Resumen.module.css";

interface Props {
  cliente: Cliente;
  /** La clave ya no sirve (401/403): se vuelve a pedir. */
  alExpirar: () => void;
}

const esAuth = (e: unknown): boolean => e instanceof ErrorApi && (e.estado === 401 || e.estado === 403);

export function Resumen({ cliente, alExpirar }: Props) {
  const [params, cambiar] = useParametrosUrl();
  const filtros = useMemo(() => leerFiltrosResumen(params), [params]);
  const clave = JSON.stringify(filtros);

  const { datos, cargando, error, recargar } = useConsulta<RespuestaResumenPanel>(clave, (senal) =>
    cliente.obtener<RespuestaResumenPanel>("/v1/panel/resumen", aParametrosResumen(filtros), senal),
  );
  const comercios = useConsulta<ComercioFiltro[]>("comercios", (senal) => cliente.obtener<ComercioFiltro[]>("/v1/panel/comercios", {}, senal));

  useEffect(() => {
    if (esAuth(error) || esAuth(comercios.error)) alExpirar();
  }, [error, comercios.error, alExpirar]);

  const aplicar = (f: FiltrosResumen) => cambiar(filtrosResumenAUrl(f));
  const t = datos?.totales;

  return (
    <section aria-label="Resumen" aria-busy={cargando}>
      <div className={estilos.encabezado}>
        <h2>Resumen</h2>
        <p>Qué se aprobó, qué quedó en revisión y qué se rechazó, con las reglas que más pesaron.</p>
      </div>

      <FiltrosResumenForm aplicados={filtros} comercios={comercios.datos ?? []} alAplicar={aplicar} />

      {error && !esAuth(error) ? (
        <div className={estilos.aviso} role="alert">
          <span>No se pudo cargar: {error.message}</span>
          <button type="button" className="btn suave" onClick={recargar}>
            Reintentar
          </button>
        </div>
      ) : null}

      {!datos || !t ? (
        cargando ? (
          <p className="cargando" role="status">
            Cargando el resumen…
          </p>
        ) : null
      ) : (
        <div className={cargando ? estilos.refrescando : undefined}>
          <div className="rejilla kpis pila">
            <div className="kpi">
              <span className="etiqueta">Pagos</span>
              <span className="valor">{numero(t.pagos)}</span>
              <span className="nota">evaluados en el periodo</span>
            </div>
            <div className="kpi verde">
              <span className="etiqueta">Aprobadas</span>
              <span className="valor">{porcentaje(t.aprobadas, t.pagos)}</span>
              <span className="nota">{numero(t.aprobadas)} pagos</span>
            </div>
            <div className="kpi">
              <span className="etiqueta">En revisión</span>
              <span className="valor">{porcentaje(t.en_revision, t.pagos)}</span>
              <span className="nota">{numero(t.en_revision)} pagos</span>
            </div>
            <div className="kpi oscuro">
              <span className="etiqueta">Rechazadas</span>
              <span className="valor">{porcentaje(t.rechazadas, t.pagos)}</span>
              <span className="nota">{numero(t.rechazadas)} pagos</span>
            </div>
            <div className="kpi">
              <span className="etiqueta">Monto rechazado</span>
              <span className="valor">{pesosCompactos(t.monto_rechazado_cop)}</span>
              <span className="nota">
                {pesos(t.monto_rechazado_cop)} de {pesosCompactos(t.monto_cop)} en total
              </span>
            </div>
            <div className="kpi">
              <span className="etiqueta">Por revisar</span>
              <span className="valor">{numero(t.revisiones_pendientes)}</span>
              <span className="nota">pagos esperando a un analista</span>
            </div>
          </div>

          <div className="rejilla dos pila">
            <section className="tarjeta" aria-labelledby="t-por-dia">
              <h3 id="t-por-dia">Pagos por día</h3>
              <p className="sub">Cada barra es un día, dividida por la decisión.</p>
              <GraficaPorDia dias={datos.por_dia} />
            </section>
            <section className="tarjeta" aria-labelledby="t-reglas">
              <h3 id="t-reglas">Reglas más activadas</h3>
              <p className="sub">Cuántos pagos tocó cada regla de seguridad.</p>
              <ListaBarras filas={datos.reglas_mas_activadas.map((r) => ({ etiqueta: r.regla, valor: r.veces }))} vacio="Todavía no se activó ninguna." />
            </section>
          </div>

          <div className="rejilla dos pila">
            <section className="tarjeta" aria-labelledby="t-puntaje">
              <h3 id="t-puntaje">Distribución del puntaje</h3>
              <p className="sub">Cuántos pagos cayeron en cada tramo de probabilidad de fraude (0 a 1).</p>
              <GraficaPuntaje cubetas={datos.distribucion_del_puntaje} />
            </section>
            <section className="tarjeta" aria-labelledby="t-calidad">
              <h3 id="t-calidad">Calidad del servicio</h3>
              <p className="sub">Señales de que el sistema trabaja con buenos datos.</p>
              <dl className="datos">
                <dt>Modo degradado</dt>
                <dd>
                  {porcentaje(t.modo_degradado, t.pagos)} ({numero(t.modo_degradado)})
                </dd>
                <dt>Sin estado del dispositivo</dt>
                <dd>{porcentaje(t.sin_estado_del_dispositivo, t.pagos)}</dd>
                <dt>Latencia media</dt>
                <dd>{conUnidad(t.latencia_media_ms, "ms")}</dd>
                <dt>Latencia p95</dt>
                <dd>{conUnidad(t.latencia_p95_ms, "ms")}</dd>
              </dl>
            </section>
          </div>

          {datos.por_analista.length ? (
            <div className="pila">
              <section className="tarjeta" aria-labelledby="t-equipo">
                <h3 id="t-equipo">Equipo de revisión</h3>
                <p className="sub">Qué resolvió cada persona en este periodo y cuánto tardó, en promedio, desde que el pago quedó en revisión.</p>
                <div className={estilos.cajaTabla}>
                  <table className={estilos.tabla}>
                    <thead>
                      <tr>
                        <th scope="col">Analista</th>
                        <th scope="col" className={estilos.num}>Resueltas</th>
                        <th scope="col" className={estilos.num}>Aprobadas</th>
                        <th scope="col" className={estilos.num}>Rechazadas</th>
                        <th scope="col" className={estilos.num}>Minutos promedio</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datos.por_analista.map((a) => (
                        <tr key={a.analista}>
                          <th scope="row">{a.analista}</th>
                          <td className={estilos.num}>{numero(a.resueltas)}</td>
                          <td className={estilos.num}>{numero(a.aprobadas)}</td>
                          <td className={estilos.num}>{numero(a.rechazadas)}</td>
                          <td className={estilos.num}>{a.minutos_promedio === null || a.minutos_promedio === undefined ? "—" : numero(a.minutos_promedio)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          ) : null}

          {datos.por_comercio.length ? (
            <div className="pila">
              <section className="tarjeta" aria-labelledby="t-comercio">
                <h3 id="t-comercio">Por comercio</h3>
                <p className="sub">Toca un comercio para ver sus pagos.</p>
                <div className={estilos.cajaTabla}>
                  <table className={estilos.tabla}>
                    <thead>
                      <tr>
                        <th scope="col">Comercio</th>
                        <th scope="col" className={estilos.num}>Pagos</th>
                        <th scope="col" className={estilos.num}>Aprobadas</th>
                        <th scope="col" className={estilos.num}>En revisión</th>
                        <th scope="col" className={estilos.num}>Rechazadas</th>
                        <th scope="col" className={estilos.num}>Monto rechazado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datos.por_comercio.map((c) => (
                        <tr key={c.comercio_id}>
                          <th scope="row">
                            {/* Transacciones nuevas aún no filtra por comercio: se abre la pantalla del panel de control que sí. */}
                            <a className={estilos.enlace} href={`/dashboard#/transacciones?comercio_id=${encodeURIComponent(String(c.comercio_id))}`}>{c.nombre ?? `Comercio ${c.comercio_id}`}</a>
                          </th>
                          <td className={estilos.num}>{numero(c.pagos)}</td>
                          <td className={estilos.num}>{numero(c.aprobadas)}</td>
                          <td className={estilos.num}>{numero(c.en_revision)}</td>
                          <td className={estilos.num}>{numero(c.rechazadas)}</td>
                          <td className={estilos.num}>{pesos(c.monto_rechazado)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
