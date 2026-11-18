import { describe, expect, it } from "vitest";
import { veredictoSinConfirmar } from "../veredicto";

const e = (transaccion: string, clave = "K1") => ({ transaccion, clave });

describe("veredicto de los pagos sin confirmar", () => {
  it("un pago que ya no está pendiente: lo dice con cautela y lo saca de la lista de bloqueados", () => {
    const r = veredictoSinConfirmar({ a: e("TX1") }, "K1", new Set(["b"]));
    expect(r.aviso?.texto).toMatch(/TX1 ya no aparece como pendiente en esta página/);
    expect(r.aviso?.texto).toMatch(/lo más probable/);
    expect(r.aviso?.error).toBe(false);
    expect(r.resto).toEqual({});
  });

  it("un pago que sigue pendiente: dice que no se guardó", () => {
    const r = veredictoSinConfirmar({ a: e("TX1") }, "K1", new Set(["a"]));
    expect(r.aviso?.texto).toMatch(/TX1 sigue pendiente: la decisión no se guardó/);
    expect(r.aviso?.error).toBe(true);
    expect(r.resto).toEqual({});
  });

  it("varios pagos: se informan TODOS, no solo el primero", () => {
    const r = veredictoSinConfirmar({ a: e("TX1"), b: e("TX2"), c: e("TX3") }, "K1", new Set(["c"]));
    expect(r.aviso?.texto).toMatch(/2 pagos \(TX1, TX2\) ya no aparecen como pendientes/);
    expect(r.aviso?.texto).toMatch(/TX3 sigue pendiente/);
    expect(r.aviso?.error).toBe(true);
  });

  it("una lectura de OTRA consulta no juzga a nadie y deja todo bloqueado", () => {
    const r = veredictoSinConfirmar({ a: e("TX1", "K1") }, "K2", new Set());
    expect(r.aviso).toBeNull();
    expect(r.resto).toEqual({ a: e("TX1", "K1") });
  });

  it("mezcla: solo se juzgan los de la misma consulta; los otros siguen bloqueados", () => {
    const r = veredictoSinConfirmar({ a: e("TX1", "K1"), b: e("TX2", "K2") }, "K1", new Set());
    expect(r.aviso?.texto).toMatch(/TX1/);
    expect(r.aviso?.texto).not.toMatch(/TX2/);
    expect(r.resto).toEqual({ b: e("TX2", "K2") });
  });
});
