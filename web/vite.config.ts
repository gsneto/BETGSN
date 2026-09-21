import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";
import { fileURLToPath, URL } from "node:url";

// O backend BETGSN (FastAPI) roda em 127.0.0.1:8787.
// O proxy evita CORS no dev e mantem a URL da API relativa no cliente.
const BACKEND = process.env.BETGSN_API_URL ?? "http://127.0.0.1:8787";

// Porta dedicada: a 5173 e usada por outros projetos do usuario e o
// strictPort faria o dev server morrer quando ela estivesse ocupada.
const PORT = Number(process.env.BETGSN_WEB_PORT ?? 5180);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: PORT,
    strictPort: true,
    proxy: {
      "/api": {
        target: BACKEND,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  preview: {
    port: PORT,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  // Testes (Vitest). Rodam em jsdom porque os componentes usam
  // fetch/AbortController/WebSocket do navegador.
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    clearMocks: true,
    unstubGlobals: true,
  },
});
