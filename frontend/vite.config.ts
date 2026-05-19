import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In docker compose the frontend container talks to "backend:8000". For
// local non-docker dev, override with VITE_BACKEND_ORIGIN=http://localhost:8000.
const backendOrigin = process.env.VITE_BACKEND_ORIGIN ?? "http://backend:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // Bind mounts from a Windows host into the Linux container don't
    // deliver inotify events reliably, so HMR misses file edits. Poll
    // instead — slightly more CPU, but edits actually reload.
    watch: { usePolling: true, interval: 250 },
    proxy: {
      "/api": {
        target: backendOrigin,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
