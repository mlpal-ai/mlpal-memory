import path from "node:path";

import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vite";

// Served by FastAPI at /ui (StaticFiles over ui-app/dist). HashRouter keeps
// client routing server-agnostic, so base + a single mount is all it takes.
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
