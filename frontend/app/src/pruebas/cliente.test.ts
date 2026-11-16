import { describe, expect, it, vi } from "vitest";
import { ErrorApi, consulta, crearCliente } from "../red/cliente";
import { respuesta } from "./ayudas";

describe("consulta", () => {
  it("omite vacíos y codifica", () => {
    expect(consulta({ a: "x y", b: "", c: null, d: undefined, e: 0 })).toBe("?a=x%20y&e=0");
    expect(consulta()).toBe("");
  });
});

describe("cliente", () => {
  it("manda la clave en la cabecera y solo hace GET", async () => {
    const f = vi.fn(async () => respuesta(200, { ok: 1 }));
    await crearCliente("K1", { fetcher: f as unknown as typeof fetch }).obtener("/v1/x", { a: 1 });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/v1/x?a=1");
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("K1");
  });

  it("reintenta un corte 503 y entrega el resultado cuando vuelve", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(respuesta(503, { detail: "caído" }))
      .mockResolvedValueOnce(respuesta(200, { ok: 1 }));
    const r = await crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [1, 1] }).obtener("/v1/x");
    expect(r).toEqual({ ok: 1 });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("se rinde tras agotar los reintentos y dice el error", async () => {
    const f = vi.fn(async () => respuesta(503, { detail: "caído" }));
    const p = crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [1, 1] }).obtener("/v1/x");
    await expect(p).rejects.toMatchObject({ estado: 503, message: "caído" });
    expect(f).toHaveBeenCalledTimes(3);
  });

  it.each([401, 403, 404, 422])("NO reintenta un %i (error del usuario)", async (estado) => {
    const f = vi.fn(async () => respuesta(estado, { detail: "no" }));
    await expect(crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [1, 1] }).obtener("/v1/x")).rejects.toBeInstanceOf(ErrorApi);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("una falla de red se informa en español y se reintenta", async () => {
    const f = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [1] }).obtener("/v1/x")).rejects.toMatchObject({
      estado: 0,
      message: "No se pudo conectar con el servidor.",
    });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("el tiempo agotado NO se reintenta (no se espera 3 veces 20 s)", async () => {
    const f = vi.fn((_u: unknown, init: RequestInit) => new Promise<Response>((_, rechazar) => init.signal?.addEventListener("abort", () => rechazar(new DOMException("x", "AbortError")))));
    await expect(crearCliente("K", { fetcher: f as unknown as typeof fetch, tiempoMax: 5, esperas: [1, 1] }).obtener("/v1/x")).rejects.toMatchObject({ agotado: true });
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("no manda cookies ni deja la respuesta en la caché, y solo acepta rutas del mismo origen", async () => {
    const f = vi.fn(async () => respuesta(200, {}));
    const cliente = crearCliente("K", { fetcher: f as unknown as typeof fetch });
    await cliente.obtener("/v1/x");
    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    for (const mala of ["https://otro.example/v1/x", "//otro.example/v1/x", "v1/x"]) {
      await expect(cliente.obtener(mala)).rejects.toMatchObject({ message: "Ruta no permitida." });
    }
    expect(f).toHaveBeenCalledTimes(1); // la clave nunca salió hacia otra dirección
  });

  it("cancelar durante la espera entre reintentos corta al instante", async () => {
    const control = new AbortController();
    const f = vi.fn(async () => respuesta(503, { detail: "caído" }));
    const p = crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [60000] }).obtener("/v1/x", {}, control.signal);
    await vi.waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    control.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });

  it("si ya viene cancelada no espera ni llama", async () => {
    const control = new AbortController();
    control.abort();
    const f = vi.fn(async () => respuesta(503, {}));
    await expect(crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [60000] }).obtener("/v1/x", {}, control.signal)).rejects.toMatchObject({ name: "AbortError" });
  });

  it("si la petición no responde a tiempo, falla con un mensaje claro", async () => {
    const f = vi.fn((_u: unknown, init: RequestInit) => new Promise<Response>((_, rechazar) => init.signal?.addEventListener("abort", () => rechazar(new DOMException("x", "AbortError")))));
    const p = crearCliente("K", { fetcher: f as unknown as typeof fetch, tiempoMax: 10, esperas: [] }).obtener("/v1/x");
    await expect(p).rejects.toMatchObject({ estado: 0, message: "La respuesta tardó demasiado." });
  });

  it("cancelar desde fuera lanza AbortError y NO reintenta", async () => {
    const control = new AbortController();
    const f = vi.fn((_u: unknown, init: RequestInit) => new Promise<Response>((_, rechazar) => init.signal?.addEventListener("abort", () => rechazar(new DOMException("x", "AbortError")))));
    const p = crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [1, 1] }).obtener("/v1/x", {}, control.signal);
    control.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("una respuesta que no es JSON no rompe: da el error con su código", async () => {
    const f = vi.fn(async () => new Response("<html>", { status: 502 }));
    await expect(crearCliente("K", { fetcher: f as unknown as typeof fetch, esperas: [] }).obtener("/v1/x")).rejects.toMatchObject({ estado: 502, message: "Error 502" });
  });
});
