/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` against a running engine: `python main.py ui --port 8765 --no-browser`,
// then CHEAPTRIP_TOKEN=<the token it prints> npm run dev
const engine = process.env.CHEAPTRIP_API ?? "http://127.0.0.1:8765";
const token = process.env.CHEAPTRIP_TOKEN ?? "";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./", // served by the engine at /, and opened from ui/dist in the app
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: {
    proxy: {
      "/api": { target: engine, changeOrigin: true, headers: token ? { Authorization: `Bearer ${token}` } : {} },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
