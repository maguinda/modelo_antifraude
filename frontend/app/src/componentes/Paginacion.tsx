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
  // «aria-disabled» y no «disabled»: un botón deshabilitado le quita el foco a quien navega con teclado o lector de pantalla.
  const ir = (destino: number, activo: boolean) => () => {
    if (activo && !ocupado) alCambiarPagina(destino);
  };
  const boton = (texto: string, destino: number, activo: boolean) => (
    <button type="button" className="btn suave" aria-disabled={!activo || ocupado} onClick={ir(destino, activo)}>
      {texto}
    </button>
  );
  return (
    <nav className={estilos.barra} aria-label="Paginación">
      <p className={estilos.estado} aria-live="polite">
        {total === 0 ? "Sin resultados" : `Mostrando ${desde}–${hasta} de ${total}`}
        {ocupado ? <span className={estilos.soloLector}> Cargando…</span> : null}
      </p>
      <div className={estilos.controles}>
        <div className={`campo ${estilos.campoCorto}`}>
          <label htmlFor="por-pagina">Por página</label>
          <select
            id="por-pagina"
            value={porPagina}
            aria-busy={ocupado}
            onChange={(e) => {
              if (!ocupado) alCambiarTamano(Number(e.target.value));
            }}
          >
            {TAMANOS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        {boton("Primera", 1, pagina > 1)}
        {boton("Anterior", pagina - 1, pagina > 1)}
        <span className={estilos.pagina}>
          Página {pagina} de {paginas}
        </span>
        {boton("Siguiente", pagina + 1, pagina < paginas)}
        {boton("Última", paginas, pagina < paginas)}
      </div>
    </nav>
  );
}
