import type { CubetaPuntaje } from "../red/api.generado";
import { useAnchoVentana } from "../hooks/useAnchoVentana";
import { numero } from "../formato";
import { anchoGrafica, maximo } from "../resumen";
import estilos from "./Graficas.module.css";

const ALTO = 190;
const MARGEN = { lado: 8, abajo: 24, arriba: 8 };

/** Cuántos pagos cayeron en cada tramo de probabilidad de fraude (0 a 1). */
export function GraficaPuntaje({ cubetas }: { cubetas: CubetaPuntaje[] }) {
  const ancho = anchoGrafica(420, useAnchoVentana());
  if (!cubetas.length) return <p className="vacio">No hay pagos en este periodo.</p>;
  const tope = maximo(cubetas.map((c) => c.pagos));
  const paso = (ancho - MARGEN.lado * 2) / cubetas.length;
  return (
    <div>
      <svg className={estilos.grafica} viewBox={`0 0 ${ancho} ${ALTO}`} role="img" aria-label="Distribución del puntaje de riesgo (los datos están en la tabla de abajo)">
        {cubetas.map((c, i) => {
          const alto = (c.pagos / tope) * (ALTO - MARGEN.abajo - MARGEN.arriba);
          const x = MARGEN.lado + paso * i + 3;
          return (
            <g key={c.desde}>
              <rect className={estilos.azul} x={x} y={ALTO - MARGEN.abajo - alto} width={paso - 6} height={Math.max(alto, 1)} rx={3}>
                <title>{`Puntaje ${c.desde.toFixed(1)} a ${c.hasta.toFixed(1)}: ${numero(c.pagos)} pagos`}</title>
              </rect>
              <text x={x + (paso - 6) / 2} y={ALTO - 7} textAnchor="middle">
                {c.desde.toFixed(1)}
              </text>
            </g>
          );
        })}
      </svg>
      <details className={estilos.datos}>
        <summary>Ver los datos en tabla</summary>
        <div className={estilos.caja}>
          <table>
            <caption className={estilos.soloLector}>Pagos por tramo de puntaje de riesgo</caption>
            <thead>
              <tr>
                <th scope="col">Tramo</th>
                <th scope="col" className={estilos.num}>Pagos</th>
              </tr>
            </thead>
            <tbody>
              {cubetas.map((c) => (
                <tr key={c.desde}>
                  <th scope="row">{`${c.desde.toFixed(1)} a ${c.hasta.toFixed(1)}`}</th>
                  <td className={estilos.num}>{numero(c.pagos)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
