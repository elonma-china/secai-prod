import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const gateway = process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8131";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
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
    },
  },
});
