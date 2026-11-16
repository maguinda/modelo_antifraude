import type { CSSProperties } from "react";
import type { PesoSenal, Senales as TipoSenales } from "../red/tipos";

function porcentaje(x: number): string {
  return `${(100 * x).toLocaleString("es-CO", { maximumFractionDigits: x < 0.1 ? 1 : 0 })} %`;
}

function Fila({ s, clase, maximo }: { s: PesoSenal; clase: "sube" | "baja"; maximo: number }) {
  const ancho = `${Math.max(6, Math.round((100 * Math.abs(s.puntos)) / maximo))}%`;
  return (
    <li className={`senal ${clase}`}>
      <div className="texto-senal">
        <span>{s.texto}</span>
        <strong className="peso-senal">
          {s.puntos > 0 ? "+" : "−"}
          {Math.abs(s.puntos).toLocaleString("es-CO", { maximumFractionDigits: 0 })} pts
        </strong>
      </div>
      <span className="pista-senal">
        <span className="barra-senal" style={{ "--ancho": ancho } as CSSProperties} />
      </span>
    </li>
  );
}

/** «Las señales que más pesaron». Devuelve null si no hay nada que mostrar (quien lo usa muestra otra explicación). */
export function haySenales(senales: TipoSenales | null | undefined): boolean {
  return Boolean(senales && ((senales.suben ?? []).length || (senales.bajan ?? []).length));
}

export function Senales({ senales, puntaje }: { senales: TipoSenales | null | undefined; puntaje: number | null }) {
  if (!senales || !haySenales(senales)) return null;
  const suben = senales.suben ?? [];
  const bajan = senales.bajan ?? [];
  const maximo = Math.max(1, ...[...suben, ...bajan].map((s) => Math.abs(s.puntos)));
  const tipico = senales.puntaje_tipico;
  return (
    <>
      {tipico !== null && tipico !== undefined && puntaje !== null ? (
        <p className="meta">
          Un pago típico tendría {porcentaje(tipico)} de probabilidad de fraude; este tiene {porcentaje(puntaje)}. Esto es lo que más empujó la diferencia:
        </p>
      ) : null}
      {suben.length ? (
        <ul className="senales">
          {suben.map((s) => (
            <Fila key={s.clave + s.texto} s={s} clase="sube" maximo={maximo} />
          ))}
        </ul>
      ) : null}
      {bajan.length ? (
        <>
          <p className="meta">A favor del pago:</p>
          <ul className="senales">
            {bajan.map((s) => (
              <Fila key={s.clave + s.texto} s={s} clase="baja" maximo={maximo} />
            ))}
          </ul>
        </>
      ) : null}
      <p className="meta aclaracion">
        Son las señales que más pesaron en el modelo: ayudan a decidir, no prueban que haya fraude. Los puntos son una estimación y pueden variar un poco.
      </p>
    </>
  );
}
