import { numero } from "../formato";
import { maximo } from "../resumen";
import estilos from "./Graficas.module.css";

export interface FilaBarra {
  etiqueta: string;
  valor: number;
}

/** Barras horizontales con su cifra: la cifra está en el texto, así que no hace falta ver la barra para leer el dato. */
export function ListaBarras({ filas, vacio }: { filas: FilaBarra[]; vacio: string }) {
  if (!filas.length) return <p className="vacio">{vacio}</p>;
  const tope = maximo(filas.map((f) => f.valor));
  return (
    <ul className={estilos.barras}>
      {filas.map((f) => (
        <li key={f.etiqueta}>
          <div className={estilos.fila}>
            <span>{f.etiqueta}</span>
            <strong>{numero(f.valor)}</strong>
          </div>
          <span className={estilos.pista} aria-hidden="true">
            <span className={estilos.relleno} style={{ width: `${Math.max(2, Math.round((100 * f.valor) / tope))}%` }} />
          </span>
        </li>
      ))}
    </ul>
  );
}
