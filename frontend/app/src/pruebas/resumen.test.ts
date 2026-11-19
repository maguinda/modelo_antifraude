import { describe, expect, it } from "vitest";
import {
  anchoGrafica,
  aParametrosResumen,
  conUnidad,
  etiquetaDia,
  filtrosResumenAUrl,
  indicesConEtiqueta,
  leerFiltrosResumen,
  maximo,
  porcentaje,
  validarFechas,
} from "../resumen";

const leer = (q: string) => leerFiltrosResumen(new URLSearchParams(q));

describe("filtros del resumen", () => {
  it("lee los filtros válidos de la dirección", () => {
    expect(leer("comercio_id=2&desde=2026-11-01&hasta=2026-11-30")).toEqual({ comercio: "2", desde: "2026-11-01", hasta: "2026-11-30" });
  });
  it("descarta lo raro en vez de mandarlo al servidor", () => {
    expect(leer("comercio_id=2;DROP&desde=ayer&hasta=2026-13")).toEqual({ comercio: "", desde: "", hasta: "" });
    expect(leer("comercio_id=1234567890")).toEqual({ comercio: "", desde: "", hasta: "" }); // 10 dígitos: no es un id
  });
  it("un rango al revés se avisa en el formulario y NO se manda al servidor", () => {
    const f = { comercio: "2", desde: "2026-12-01", hasta: "2026-11-01" };
    expect(validarFechas(f)).toMatch(/no puede ser posterior/);
    expect(aParametrosResumen(f)).toEqual({ comercio_id: "2" });
  });
  it("un rango bien puesto, o con un solo extremo, sí se manda", () => {
    expect(aParametrosResumen({ comercio: "", desde: "2026-11-01", hasta: "2026-11-30" })).toEqual({ desde: "2026-11-01", hasta: "2026-11-30" });
    expect(aParametrosResumen({ comercio: "", desde: "", hasta: "2026-11-30" })).toEqual({ hasta: "2026-11-30" });
    expect(validarFechas({ desde: "2026-11-01", hasta: "2026-11-01" })).toBe(""); // el mismo día es válido
  });
  it("a la dirección solo va lo que se puso", () => {
    expect(filtrosResumenAUrl({ comercio: "", desde: "2026-11-01", hasta: "" })).toEqual({ comercio_id: null, desde: "2026-11-01", hasta: null });
  });
});

describe("números y etiquetas", () => {
  it("el porcentaje redondea y no divide por cero", () => {
    expect(porcentaje(1, 3)).toBe("33 %");
    expect(porcentaje(2, 3)).toBe("67 %");
    expect(porcentaje(0, 0)).toBe("—");
    expect(porcentaje(5, 0)).toBe("—");
  });
  it("una medida sin dato se muestra como raya, no como null ni NaN", () => {
    expect(conUnidad(null, "ms")).toBe("—");
    expect(conUnidad(undefined, "ms")).toBe("—");
    expect(conUnidad(12.345, "ms")).toBe("12.3 ms");
    expect(conUnidad(0, "ms")).toBe("0.0 ms"); // cero es un dato, no ausencia
  });
  it("el máximo nunca es menor que 1", () => {
    expect(maximo([])).toBe(1);
    expect(maximo([0, 0])).toBe(1);
    expect(maximo([3, 9, 4])).toBe(9);
  });
  it("las etiquetas del eje: todas si caben, y si no una de cada tantas (siempre la primera)", () => {
    expect(etiquetaDia("2026-11-05")).toBe("05/11");
    expect(etiquetaDia("raro")).toBe("raro");
    expect([...indicesConEtiqueta(5, 8)]).toEqual([0, 1, 2, 3, 4]);
    const treinta = [...indicesConEtiqueta(30, 8)];
    expect(treinta[0]).toBe(0);
    expect(treinta.length).toBeLessThanOrEqual(8);
    expect(indicesConEtiqueta(0, 8).size).toBe(0);
  });
  it("en pantallas angostas la gráfica es más estrecha", () => {
    expect(anchoGrafica(640, 320)).toBe(300);
    expect(anchoGrafica(640, 1280)).toBe(640);
  });
});
