import { TAMANOS, paginasDe, rango } from "../paginacion";
import estilos from "./Paginacion.module.css";

interface Props {
  total: number;
  pagina: number;
  porPagina: number;
  /** Mientras carga se bloquean los botones (nada de doble clic que mande dos peticiones). */
  ocupado?: boolean;
  alCambiarPagina: (pagina: number) => void;
  alCambiarTamano: (porPagina: number) => void;
}

export function Paginacion({ total, pagina, porPagina, ocupado = false, alCambiarPagina, alCambiarTamano }: Props) {
  const paginas = paginasDe(total, porPagina);
  const [desde, hasta] = rango(pagina, total, porPagina);
  return (
    <nav className={estilos.barra} aria-label="Paginación">
      <p className={estilos.estado} aria-live="polite">
        {total === 0 ? "Sin resultados" : `Mostrando ${desde}–${hasta} de ${total}`}
      </p>
      <div className={estilos.controles}>
        <div className={`campo ${estilos.campoCorto}`}>
          <label htmlFor="por-pagina">Por página</label>
          <select id="por-pagina" value={porPagina} onChange={(e) => alCambiarTamano(Number(e.target.value))} disabled={ocupado}>
            {TAMANOS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <button type="button" className="btn suave" disabled={ocupado || pagina <= 1} onClick={() => alCambiarPagina(1)}>
          Primera
        </button>
        <button type="button" className="btn suave" disabled={ocupado || pagina <= 1} onClick={() => alCambiarPagina(pagina - 1)}>
          Anterior
        </button>
        <span className={estilos.pagina}>
          Página {pagina} de {paginas}
        </span>
        <button
          type="button"
          className="btn suave"
          disabled={ocupado || pagina >= paginas}
          onClick={() => alCambiarPagina(pagina + 1)}
        >
          Siguiente
        </button>
        <button
          type="button"
          className="btn suave"
          disabled={ocupado || pagina >= paginas}
          onClick={() => alCambiarPagina(paginas)}
        >
          Última
        </button>
      </div>
    </nav>
  );
}
