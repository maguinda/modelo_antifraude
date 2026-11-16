import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useConsulta } from "../hooks/useConsulta";

function diferido<T>() {
  let resolver!: (v: T) => void;
  let rechazar!: (e: Error) => void;
  const promesa = new Promise<T>((res, rej) => {
    resolver = res;
    rechazar = rej;
  });
  return { promesa, resolver, rechazar };
}

describe("useConsulta", () => {
  it("una respuesta vieja que llega tarde NO pisa a la nueva", async () => {
    const a = diferido<string>();
    const b = diferido<string>();
    const { result, rerender } = renderHook(({ k }) => useConsulta(k, () => (k === "a" ? a.promesa : b.promesa)), { initialProps: { k: "a" } });
    rerender({ k: "b" });
    await act(async () => {
      b.resolver("B");
    });
    await waitFor(() => expect(result.current.datos).toBe("B"));
    await act(async () => {
      a.resolver("A-vieja");
    });
    expect(result.current.datos).toBe("B");
  });

  it("conserva los datos anteriores mientras carga lo siguiente y si falla", async () => {
    const siguiente = diferido<string>();
    const { result, rerender } = renderHook(({ k }) => useConsulta(k, () => (k === "1" ? Promise.resolve("uno") : siguiente.promesa)), { initialProps: { k: "1" } });
    await waitFor(() => expect(result.current.datos).toBe("uno"));
    rerender({ k: "2" });
    await waitFor(() => expect(result.current.cargando).toBe(true));
    expect(result.current.datos).toBe("uno"); // no se vacía
    await act(async () => {
      siguiente.rechazar(new Error("falló"));
    });
    await waitFor(() => expect(result.current.error?.message).toBe("falló"));
    expect(result.current.datos).toBe("uno");
    expect(result.current.cargando).toBe(false);
  });

  it("recargar vuelve a pedir", async () => {
    let n = 0;
    const { result } = renderHook(() => useConsulta("k", async () => ++n));
    await waitFor(() => expect(result.current.datos).toBe(1));
    act(() => result.current.recargar());
    await waitFor(() => expect(result.current.datos).toBe(2));
  });
});
