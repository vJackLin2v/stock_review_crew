import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 端口协调（2026-08-08）：5173 已被其他项目占用 → 5174
    port: 5174,
    // 代理指向本项目后端（8501），勿指向其他项目
    proxy: { "/api": "http://127.0.0.1:8501" },
  },
  build: { outDir: "dist" },
});
