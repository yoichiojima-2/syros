import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build output is committed into the Python package so `pip install syros`
// and the Docker image need no Node toolchain.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/syros/console/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8484" } },
});
