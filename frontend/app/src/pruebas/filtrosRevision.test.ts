import { describe, expect, it } from "vitest";
import { FILTROS_INICIALES, aParametrosApi, filtrosAUrl, hayFiltros, leerFiltros, validarFiltros } from "../filtrosRevision";

const url = (s: string) => new URLSearchParams(s);

describe("leerFiltros: lo que llega de la dirección se sanea", () => {
  it("sin nada da los valores iniciales", () => {
    expect(leerFiltros(url(""))).toEqual(FILTROS_INICIALES);
  });
  it("valores raros se descartan sin error", () => {
    const f = leerFiltros(url("estado=borrar&orden=drop&riesgo=zzz&monto_min=abc&desde=ayer&hasta=2026-13&comercio_id=1;DROP"));
    expect(f).toEqual(FILTROS_INICIALES);
  });
  it("valores válidos se conservan", () => {
    const f = leerFiltros(url("estado=resuelta&orden=monto_desc&riesgo=alto&monto_min=1500.5&desde=2026-11-01&hasta=2026-11-30&comercio_id=7&q=%20AF-1%20"));
    expect(f).toMatchObject({ estado: "resuelta", orden: "monto_desc", riesgo: "alto", montoMin: "1500.5", desde: "2026-11-01", hasta: "2026-11-30", comercio: "7", q: "AF-1" });
  });
  it("un monto negativo o infinito se descarta y el texto largo se recorta a 64", () => {
    expect(leerFiltros(url("monto_min=-5")).montoMin).toBe("");
    expect(leerFiltros(url("monto_min=Infinity")).montoMin).toBe("");
    expect(leerFiltros(url("q=" + "x".repeat(300))).q).toHaveLength(64);
  });
});

describe("aParametrosApi", () => {
  it("lo básico: estado, página y orden", () => {
    expect(aParametrosApi(FILTROS_INICIALES, 25, 50)).toEqual({ estado: "pendiente", limite: 25, desplazamiento: 50, orden: "antiguos" });
  });
  it.each([
    ["alto", { riesgo_min: 0.6 }],
    ["medio", { riesgo_min: 0.35, riesgo_max: 0.5999 }],
    ["bajo", { riesgo_max: 0.3499 }],
  ] as const)("el riesgo %s se traduce a rangos del servidor", (riesgo, esperado) => {
    expect(aParametrosApi({ ...FILTROS_INICIALES, riesgo }, 25, 0)).toMatchObject(esperado);
  });
  it("un rango de fechas al revés (enlace viejo) se ignora en vez de dar error", () => {
    const p = aParametrosApi({ ...FILTROS_INICIALES, desde: "2026-12-01", hasta: "2026-11-01" }, 25, 0);
    expect(p.desde).toBeUndefined();
    expect(p.hasta).toBeUndefined();
  });
  it("manda comercio, monto y búsqueda cuando están", () => {
    expect(aParametrosApi({ ...FILTROS_INICIALES, comercio: "3", montoMin: "0", q: "AF" }, 25, 0)).toMatchObject({ comercio_id: "3", monto_min: "0", q: "AF" });
  });
});

describe("validarFiltros y hayFiltros", () => {
  it("valida con los mismos criterios que el servidor", () => {
    expect(validarFiltros({ montoMin: "-1", desde: "", hasta: "" }).monto).not.toBe("");
    expect(validarFiltros({ montoMin: "abc", desde: "", hasta: "" }).monto).not.toBe("");
    expect(validarFiltros({ montoMin: "0", desde: "", hasta: "" }).monto).toBe("");
    expect(validarFiltros({ montoMin: "", desde: "2026-12-01", hasta: "2026-11-01" }).fechas).not.toBe("");
    expect(validarFiltros({ montoMin: "", desde: "2026-11-01", hasta: "2026-11-01" }).fechas).toBe("");
  });
  it("estado y orden no cuentan como filtros", () => {
    expect(hayFiltros({ ...FILTROS_INICIALES, estado: "resuelta", orden: "monto_desc" })).toBe(false);
    expect(hayFiltros({ ...FILTROS_INICIALES, montoMin: "0" })).toBe(true);
    expect(hayFiltros({ ...FILTROS_INICIALES, riesgo: "bajo" })).toBe(true);
  });
  it("ida y vuelta por la dirección", () => {
    const f = { ...FILTROS_INICIALES, estado: "resuelta" as const, riesgo: "medio" as const, q: "AF", montoMin: "10" };
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(filtrosAUrl(f))) if (v !== null) u.set(k, v);
    expect(leerFiltros(u)).toEqual(f);
    expect(Object.values(filtrosAUrl(FILTROS_INICIALES)).every((v) => v === null)).toBe(true); // lo inicial no ensucia la dirección
  });
});
