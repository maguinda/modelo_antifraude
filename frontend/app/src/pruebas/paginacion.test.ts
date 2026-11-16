import { describe, expect, it } from "vitest";
import { MAX_DESPLAZAMIENTO, ajustarPagina, hayMasAllaDelTope, paginasDe, rango, tamanoValido } from "../paginacion";

describe("paginación", () => {
  it("cuenta páginas (mínimo una, aunque no haya nada)", () => {
    expect(paginasDe(0, 25)).toBe(1);
    expect(paginasDe(25, 25)).toBe(1);
    expect(paginasDe(26, 25)).toBe(2);
    expect(paginasDe(390, 25)).toBe(16);
  });
  it.each([
    ["texto", 100, 1],
    [null, 100, 1],
    ["0", 100, 1],
    ["-5", 100, 1],
    ["2.9", 100, 2],
    ["999", 100, 4],
    ["3", 0, 1],
  ])("la página %s con total %i cae en %i", (valor, total, esperada) => {
    expect(ajustarPagina(valor, total, 25)).toBe(esperada);
  });
  it("solo acepta los tamaños permitidos", () => {
    expect(tamanoValido("50")).toBe(50);
    expect(tamanoValido("9999")).toBe(25);
    expect(tamanoValido(null)).toBe(25);
  });
  it("el rango mostrado es el de la página", () => {
    expect(rango(1, 0, 25)).toEqual([0, 0]);
    expect(rango(2, 30, 25)).toEqual([26, 30]);
  });
  it("nunca pide más allá del tope del servidor (100.000 filas), con cualquier tamaño de página", () => {
    for (const tam of [10, 25, 50]) {
      const ultima = ajustarPagina(9999999, 3_000_000, tam);
      expect((ultima - 1) * tam).toBeLessThanOrEqual(MAX_DESPLAZAMIENTO);
      expect(paginasDe(3_000_000, tam)).toBe(ultima);
      expect(hayMasAllaDelTope(3_000_000, tam)).toBe(true);
    }
    expect(hayMasAllaDelTope(390, 25)).toBe(false);
  });
});
