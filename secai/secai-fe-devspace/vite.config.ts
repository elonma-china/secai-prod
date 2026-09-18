import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const gateway = process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8131";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Cloudflare quick tunnel + LAN access
    allowedHosts: [
      "graphics-accent-collective-instantly.trycloudflare.com",
      ".trycloudflare.com",
      "localhost",
      "127.0.0.1",
    ],
    // Avoid ENOSPC when host inotify watchers are exhausted.
    watch: {
      usePolling: true,
      interval: 1000,
    },
    proxy: {
      "/ai": {
        target: gateway,
        changeOrigin: true,
        // Avoid nginx/proxy buffering of SSE.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            const ct = proxyRes.headers["content-type"] || "";
            if (String(ct).includes("text/event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache, no-transform";
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
      "/health": { target: gateway, changeOrigin: true },
      "/poc": { target: gateway, changeOrigin: true },
      "/projects": { target: gateway, changeOrigin: true },
      "/sessions": { target: gateway, changeOrigin: true },
      "/admin": { target: gateway, changeOrigin: true },
    },
  },
});
