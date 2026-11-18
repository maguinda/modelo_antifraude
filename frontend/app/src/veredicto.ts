/** Qué se dice de los pagos cuya decisión quedó «sin confirmar» (no se supo si el servidor la guardó), una vez que llega una lectura nueva de la lista. */

export interface SinConfirmar {
  /** Identificador del pago, para nombrarlo en el aviso. */
  transaccion: string;
  /** La consulta (filtros y página) en la que se veía la tarjeta cuando se pulsó. */
  clave: string;
}

export interface AvisoVeredicto {
  texto: string;
  error: boolean;
}

const lista = (x: string[]) => x.join(", ");

/**
 * Solo se juzga con una lectura de la MISMA consulta: que un pago no aparezca en otro filtro u otra página no dice nada de si se guardó.
 * Los de otra consulta quedan bloqueados (`resto`) hasta que se mire la suya, o hasta que alguien pulse «Actualizar».
 * Aun con la misma consulta, que el pago ya no aparezca puede deberse a que la lista se corrió: por eso se habla de «lo más probable».
 */
export function veredictoSinConfirmar(
  entradas: Record<string, SinConfirmar>,
  claveDeLaLectura: string,
  pendientes: ReadonlySet<string>,
): { aviso: AvisoVeredicto | null; resto: Record<string, SinConfirmar> } {
  const resto: Record<string, SinConfirmar> = {};
  const ya: string[] = [];
  const siguen: string[] = [];
  for (const [id, e] of Object.entries(entradas)) {
    if (e.clave !== claveDeLaLectura) resto[id] = e;
    else (pendientes.has(id) ? siguen : ya).push(e.transaccion);
  }
  if (!ya.length && !siguen.length) return { aviso: null, resto };
  const partes: string[] = [];
  if (ya.length === 1)
    partes.push(
      `El pago ${ya[0]} ya no aparece como pendiente en esta página: lo más probable es que se haya guardado una decisión (la tuya o la de otra persona). Míralo en «Resueltas».`,
    );
  else if (ya.length > 1)
    partes.push(
      `${ya.length} pagos (${lista(ya)}) ya no aparecen como pendientes en esta página: lo más probable es que se haya guardado una decisión en cada uno (la tuya o la de otra persona). Míralos en «Resueltas».`,
    );
  if (siguen.length === 1) partes.push(`El pago ${siguen[0]} sigue pendiente: la decisión no se guardó. Puedes decidir de nuevo.`);
  else if (siguen.length > 1) partes.push(`${siguen.length} pagos (${lista(siguen)}) siguen pendientes: no se guardó la decisión. Puedes decidir de nuevo.`);
  return { aviso: { texto: partes.join(" "), error: siguen.length > 0 }, resto };
}
