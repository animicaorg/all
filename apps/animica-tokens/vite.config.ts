import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const serverPort = Number(process.env.ANIMICA_TOKENS_SERVER_PORT || 8787);

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${serverPort}`,
        changeOrigin: true
      },
      "/ipfs": {
        target: `http://127.0.0.1:${serverPort}`,
        changeOrigin: true
      }
    }
  }
});
