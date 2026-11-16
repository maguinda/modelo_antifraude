import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

// Las pantallas se entregan en /estaticos/app/ (mismo origen que la API: no hace falta CORS).
// En desarrollo, `npm run dev` reenvía la API y el diseño compartido a un servidor local (por omisión el puerto 8000).
export default defineConfig(({ mode }) => {
  const destino = loadEnv(mode, ".", "MODELO_IA_").MODELO_IA_DESTINO ?? "http://127.0.0.1:8000";
  return {
    base: "/estaticos/app/",
    plugins: [react()],
    build: { outDir: "dist", sourcemap: false },
    server: { proxy: { "/v1": destino, "/estaticos/comun": destino, "/estaticos/favicon.png": destino } },
    test: { environment: "jsdom", globals: false, setupFiles: ["./src/pruebas/preparar.ts"], css: false },
  };
});
