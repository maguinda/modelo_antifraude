import { useState } from "react";
import { ErrorApi } from "../red/cliente";
import type { DecisionAnalista, Revision } from "../red/tipos";
import { ETIQUETAS_DATO, hace, nivelRiesgo, pesos, valorDato } from "../formato";
import { Senales, haySenales } from "./Senales";
import estilos from "./TarjetaRevision.module.css";

export type ResultadoResolver =
  | { tipo: "ok"; decision: DecisionAnalista }
  | { tipo: "conflicto" } // otra persona ya la resolvió
  | { tipo: "sin_confirmar"; mensaje: string } // no hubo respuesta: NO se sabe si se guardó
  | { tipo: "error"; mensaje: string };

interface Props {
  rev: Revision;
  /** Envía la decisión. Nunca lanza: devuelve qué pasó (la pantalla decide el aviso y si recarga la lista). */
  alResolver: (rev: Revision, decision: DecisionAnalista, nota: string | null) => Promise<ResultadoResolver>;
}

const TEXTO: Record<DecisionAnalista, { verbo: string; clase: "ok" | "mal"; boton: string }> = {
  APROBADA: { verbo: "aprobar", clase: "ok", boton: "Aprobar" },
  RECHAZADA: { verbo: "rechazar", clase: "mal", boton: "Rechazar" },
};

export function TarjetaRevision({ rev, alResolver }: Props) {
  const [nota, setNota] = useState("");
  const [confirmando, setConfirmando] = useState<DecisionAnalista | null>(null);
  const [enviando, setEnviando] = useState(false);
  const pct = rev.puntaje_riesgo === null || rev.puntaje_riesgo === undefined ? null : Math.round(rev.puntaje_riesgo * 100);
  const nivel = nivelRiesgo(rev.puntaje_riesgo);
  const resumen = rev.resumen_pago ?? {};
  const meta = [rev.id_seguimiento, rev.id_transaccion, String(resumen.id_cliente ?? ""), hace(rev.creada_en)].filter(Boolean).join(" · ");
  const porQue = haySenales(rev.senales) || rev.motivos.length > 0 || rev.reglas_activadas.length > 0;

  const pulsar = async (decision: DecisionAnalista) => {
    if (enviando) return; // el doble clic no manda dos escrituras
    if (confirmando !== decision) {
      setConfirmando(decision); // primer clic: pide confirmar
      return;
    }
    setEnviando(true);
    const r = await alResolver(rev, decision, nota.trim() ? nota : null);
    // si salió bien, la lista se recarga y esta tarjeta desaparece; en cualquier otro caso se puede volver a intentar
    if (r.tipo !== "ok") {
      setEnviando(false);
      setConfirmando(null);
    }
  };
  const otra = (d: DecisionAnalista): DecisionAnalista => (d === "APROBADA" ? "RECHAZADA" : "APROBADA");

  return (
    <li>
      <details className={estilos.item}>
        <summary>
          <div className={estilos.filaCola}>
            <span className={`${estilos.medidor} ${nivel ? estilos[nivel] : ""}`} aria-label={pct === null ? "Sin puntaje" : `Riesgo ${pct} por ciento`}>
              {pct === null ? "—" : `${pct}%`}
            </span>
            <div className={estilos.cuerpo}>
              <div className={estilos.linea1}>
                <span className={estilos.monto}>{pesos(typeof resumen.monto_cop === "number" ? resumen.monto_cop : 0)}</span>
                <span className="chip">{rev.comercio ?? `Comercio ${rev.comercio_id}`}</span>
                {pct === null ? <span className="chip falta">Sin puntaje (modo degradado)</span> : null}
              </div>
              <div className={estilos.meta}>{meta}</div>
              {rev.reglas_activadas.length ? (
                <div className={estilos.reglas}>
                  {rev.reglas_activadas.map((x) => (
                    <span key={x.regla} className="chip regla">
                      {x.regla}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
            <span className={estilos.abrir} aria-hidden="true">
              ›
            </span>
          </div>
        </summary>

        <div className={estilos.detalle}>
          <div className={estilos.colIzq}>
            <section>
              <h3>Por qué dudó</h3>
              <Senales senales={rev.senales} puntaje={rev.puntaje_riesgo} />
              {rev.motivos.map((m) => (
                <div key={m} className={estilos.motivo}>
                  <strong>{m}</strong>
                </div>
              ))}
              {rev.reglas_activadas.map((x) => (
                <div key={x.regla} className={estilos.motivo}>
                  <strong>{x.regla}</strong>
                  <p>
                    {x.motivo} ({x.accion})
                  </p>
                </div>
              ))}
              {!porQue ? <p className={estilos.meta}>El modelo no vio una causa concreta: la probabilidad quedó en la zona de duda.</p> : null}
            </section>
            <section>
              <h3>Datos del pago</h3>
              <dl className={estilos.rejillaDatos}>
                {Object.entries(ETIQUETAS_DATO).map(([k, etiqueta]) => {
                  const v = resumen[k];
                  return v === undefined || v === null ? null : (
                    <div key={k} className={estilos.dato}>
                      <dt>{etiqueta}</dt>
                      <dd>{valorDato(k, v)}</dd>
                    </div>
                  );
                })}
              </dl>
            </section>
          </div>

          <section className={estilos.decision}>
            <h3>Tu decisión</h3>
            {rev.estado === "pendiente" ? (
              <>
                <label htmlFor={`nota-${rev.id_evaluacion}`}>Nota (opcional)</label>
                <textarea id={`nota-${rev.id_evaluacion}`} maxLength={500} value={nota} onChange={(e) => setNota(e.target.value)} />
                <p className={estilos.meta}>{nota.length} de 500 caracteres</p>
                <div className={estilos.botones}>
                  {(["APROBADA", "RECHAZADA"] as const).map((d) => {
                    const t = TEXTO[d];
                    const pendiente = confirmando === d;
                    const cancela = confirmando === otra(d);
                    return (
                      <button
                        key={d}
                        type="button"
                        className={`${estilos[t.clase]} ${pendiente ? estilos.confirmar : ""}`}
                        aria-disabled={enviando}
                        onClick={() => (cancela ? setConfirmando(null) : void pulsar(d))}
                      >
                        {cancela ? "Cancelar" : pendiente ? `Confirmar: ${t.verbo}` : t.boton}
                      </button>
                    );
                  })}
                </div>
                {confirmando ? (
                  <p className={estilos.meta} role="status">
                    Pulsa «Confirmar: {TEXTO[confirmando].verbo}» para guardar la decisión, o «Cancelar».
                  </p>
                ) : null}
              </>
            ) : (
              <p className={estilos.meta}>
                {rev.resuelta_por
                  ? `Resuelta por ${rev.resuelta_por}${rev.resuelta_en ? ` · ${hace(rev.resuelta_en)}` : ""}.`
                  : "Este pago ya fue resuelto."}
              </p>
            )}
            {rev.id_seguimiento ? (
              <a className="btn suave" href={`/dashboard#/pago/${encodeURIComponent(rev.id_seguimiento)}`}>
                Ver todo en el dashboard →
              </a>
            ) : null}
          </section>
        </div>
      </details>
    </li>
  );
}

/** Traduce lo que respondió (o no) el servidor a un resultado para la pantalla. */
export function resultadoDeError(e: unknown): ResultadoResolver {
  if (e instanceof ErrorApi) {
    if (e.estado === 409) return { tipo: "conflicto" };
    if (e.estado === 0)
      return { tipo: "sin_confirmar", mensaje: "No se pudo confirmar si se guardó la decisión. Actualiza la lista antes de volver a intentarlo." };
    return { tipo: "error", mensaje: e.message };
  }
  return { tipo: "error", mensaje: e instanceof Error ? e.message : "Error desconocido." };
}
