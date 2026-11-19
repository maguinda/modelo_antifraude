import type { ResumenDia } from "../red/api.generado";
import { useAnchoVentana } from "../hooks/useAnchoVentana";
import { numero } from "../formato";
import { anchoGrafica, etiquetaDia, indicesConEtiqueta, maximo } from "../resumen";
import estilos from "./Graficas.module.css";

const ALTO = 230;
const MARGEN = { izq: 34, abajo: 24, arriba: 8, der: 6 };

const CAPAS = [
  ["aprobadas", "Aprobada", estilos.aprobada, "aprobada"],
  ["en_revision", "En revisión", estilos.revision, "revision"],
  ["rechazadas", "Rechazada", estilos.rechazada, "rechazada"],
] as const;

/** Pagos por día: una barra por día, dividida por la decisión. Con su tabla de datos debajo (la gráfica sola no la lee un lector de pantalla). */
export function GraficaPorDia({ dias }: { dias: ResumenDia[] }) {
  const ancho = anchoGrafica(640, useAnchoVentana());
  if (!dias.length) return <p className="vacio">No hay pagos en este periodo.</p>;

  const areaAncho = ancho - MARGEN.izq - MARGEN.der;
  const areaAlto = ALTO - MARGEN.abajo - MARGEN.arriba;
  const tope = maximo(dias.map((d) => d.aprobadas + d.en_revision + d.rechazadas));
  const paso = areaAncho / dias.length;
  const anchoBarra = Math.max(3, Math.min(28, paso * 0.68));
  const conEtiqueta = indicesConEtiqueta(dias.length, ancho < 400 ? 5 : 8);

  return (
    <div>
      <svg className={estilos.grafica} viewBox={`0 0 ${ancho} ${ALTO}`} role="img" aria-label="Pagos por día según la decisión (los datos están en la tabla de abajo)">
        {[0, 0.5, 1].map((f) => {
          const y = MARGEN.arriba + areaAlto * (1 - f);
          return (
            <g key={f}>
              <line className={estilos.eje} x1={MARGEN.izq} x2={ancho - MARGEN.der} y1={y} y2={y} />
              <text x={MARGEN.izq - 6} y={y + 4} textAnchor="end">
                {numero(Math.round(tope * f))}
              </text>
            </g>
          );
        })}
        {dias.map((d, i) => {
          const x = MARGEN.izq + paso * i + (paso - anchoBarra) / 2;
          let base = ALTO - MARGEN.abajo;
          return (
            <g key={d.dia}>
              {CAPAS.map(([campo, nombre, clase]) => {
                const alto = (d[campo] / tope) * areaAlto;
                if (alto <= 0) return null;
                base -= alto;
                return (
                  <rect key={campo} className={clase} x={x} y={base} width={anchoBarra} height={alto} rx={2}>
                    <title>{`${d.dia} · ${nombre}: ${d[campo]}`}</title>
                  </rect>
                );
              })}
              {conEtiqueta.has(i) ? (
                <text x={x + anchoBarra / 2} y={ALTO - 6} textAnchor="middle">
                  {etiquetaDia(d.dia)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <ul className={estilos.leyenda}>
        {CAPAS.map(([campo, nombre, , clase]) => (
          <li key={campo} className={estilos[clase]}>
            {nombre}
          </li>
        ))}
      </ul>
      <details className={estilos.datos}>
        <summary>Ver los datos en tabla</summary>
        <div className={estilos.caja}>
          <table>
            <caption className={estilos.soloLector}>Pagos por día según la decisión</caption>
            <thead>
              <tr>
                <th scope="col">Día</th>
                <th scope="col" className={estilos.num}>Aprobadas</th>
                <th scope="col" className={estilos.num}>En revisión</th>
                <th scope="col" className={estilos.num}>Rechazadas</th>
              </tr>
            </thead>
            <tbody>
              {dias.map((d) => (
                <tr key={d.dia}>
                  <th scope="row">{d.dia}</th>
                  <td className={estilos.num}>{numero(d.aprobadas)}</td>
                  <td className={estilos.num}>{numero(d.en_revision)}</td>
                  <td className={estilos.num}>{numero(d.rechazadas)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
