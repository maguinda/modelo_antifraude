import { useEffect, useRef, useState } from "react";
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
  /** La pantalla no sabe si la decisión anterior se guardó y está mirando cómo quedó: mientras tanto no se puede decidir. */
  bloqueada?: boolean;
}

/** Tiempo mínimo entre armar la confirmación y poder confirmar: un doble toque no se salta el aviso. */
export const ESPERA_CONFIRMAR_MS = 600;

const TEXTO: Record<DecisionAnalista, { verbo: string; clase: "ok" | "mal"; boton: string }> = {
  APROBADA: { verbo: "aprobar", clase: "ok", boton: "Aprobar" },
  RECHAZADA: { verbo: "rechazar", clase: "mal", boton: "Rechazar" },
};

export function TarjetaRevision({ rev, alResolver, bloqueada = false }: Props) {
  const [nota, setNota] = useState("");
  const [confirmando, setConfirmando] = useState<DecisionAnalista | null>(null);
  const [enviando, setEnviando] = useState(false);
  const enviandoYa = useRef(false); // sincrónico: dos eventos en el mismo instante no ven el estado de React ya actualizado
  const armadaEn = useRef(0);
  const bloqueado = enviando || bloqueada;
  // Durante la pausa anti doble toque «Confirmar» se anuncia como no disponible (no queda un botón que parece activo y no responde).
  const [enPausa, setEnPausa] = useState(false);
  useEffect(() => {
    if (!confirmando) {
      setEnPausa(false);
      return;
    }
    setEnPausa(true);
    const t = setTimeout(() => setEnPausa(false), ESPERA_CONFIRMAR_MS);
    return () => clearTimeout(t);
  }, [confirmando]);
  const pct = rev.puntaje_riesgo === null || rev.puntaje_riesgo === undefined ? null : Math.round(rev.puntaje_riesgo * 100);
  const nivel = nivelRiesgo(rev.puntaje_riesgo);
  const resumen = rev.resumen_pago ?? {};
  const meta = [rev.id_seguimiento, rev.id_transaccion, String(resumen.id_cliente ?? ""), hace(rev.creada_en)].filter(Boolean).join(" · ");
  const porQue = haySenales(rev.senales) || rev.motivos.length > 0 || rev.reglas_activadas.length > 0;

  const pulsar = async (decision: DecisionAnalista) => {
    if (enviandoYa.current || bloqueada) return; // el doble clic no manda dos escrituras
    if (confirmando !== decision) {
      armadaEn.current = performance.now();
      setConfirmando(decision); // primer clic: pide confirmar
      return;
    }
    if (performance.now() - armadaEn.current < ESPERA_CONFIRMAR_MS) return; // un doble toque no confirma a ciegas
    enviandoYa.current = true;
    setEnviando(true);
    const r = await alResolver(rev, decision, nota.trim() ? nota : null);
    // si salió bien, la lista se recarga y esta tarjeta desaparece; en cualquier otro caso se puede volver a intentar
    // (si no se sabe si se guardó, la pantalla la deja `bloqueada` hasta mirar cómo quedó)
    if (r.tipo !== "ok") {
      enviandoYa.current = false;
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
            <span className={`${estilos.medidor} ${nivel ? estilos[nivel] : ""}`} role="img" aria-label={pct === null ? "Sin puntaje" : `Riesgo ${pct} por ciento`}>
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
                        aria-disabled={bloqueado || (pendiente && enPausa)}
                        onKeyDown={(e) => {
                          if (e.repeat) e.preventDefault(); // una tecla mantenida pulsada no repite el clic (armar y confirmar sin soltar)
                        }}
                        onClick={() => (cancela ? setConfirmando(null) : void pulsar(d))}
                      >
                        {cancela ? "Cancelar" : pendiente ? `Confirmar: ${t.verbo}` : t.boton}
                      </button>
                    );
                  })}
                </div>
                {/* La región existe SIEMPRE (vacía) y solo cambia su contenido: los lectores de pantalla no anuncian de forma fiable una región que aparece ya con el texto puesto. */}
                <div role="status">
                  {confirmando ? (
                    <p className={estilos.meta}>
                      Pulsa «Confirmar: {TEXTO[confirmando].verbo}» para guardar la decisión, o «Cancelar».
                    </p>
                  ) : null}
                </div>
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
    // sin respuesta (0) o con un corte del proxy (502/503/504): el servidor pudo haber guardado la decisión
    if (e.estado === 0 || e.estado === 502 || e.estado === 503 || e.estado === 504)
      return { tipo: "sin_confirmar", mensaje: "No se pudo confirmar si se guardó la decisión. Actualiza la lista antes de volver a intentarlo." };
    return { tipo: "error", mensaje: e.message };
  }
  return { tipo: "error", mensaje: e instanceof Error ? e.message : "Error desconocido." };
}
