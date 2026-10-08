import { defineConfig } from "vite";
export default defineConfig({
  base: "./",
  define: {
    __DATA_MODE__: JSON.stringify(
      process.env.PFAS_DATA_MODE === "snapshot" ? "snapshot" : "live",
    ),
  },
  server: {
    proxy: {
      "/api": `http://${process.env.PFAS_API_HOST || "127.0.0.1"}:${process.env.PFAS_API_PORT || 8000}`,
    },
  },
});
