import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 将 /api 代理到 :8000 的 FastAPI 后端，使浏览器可使用相对路径调用。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
