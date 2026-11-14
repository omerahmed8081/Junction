import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The browser only ever talks to the dev server. Vite forwards /api to the
// kitchen service and /gateway to the gateway (used by the demo reset only),
// so there is no CORS setup and no backend address in the built code.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const kitchen = env.KITCHEN_URL || "http://localhost:8002";
  const gateway = env.GATEWAY_URL || "http://localhost:8001";
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target: kitchen, changeOrigin: true },
        "/gateway": { target: gateway, changeOrigin: true },
      },
    },
  };
});
