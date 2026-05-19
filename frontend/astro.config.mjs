import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwind from "@astrojs/tailwind";
import node from "@astrojs/node";

export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
  integrations: [
    react(),
    tailwind(),
  ],
  server: { port: 4321, host: "127.0.0.1" },
  vite: {
    define: {
      "import.meta.env.API_URL": JSON.stringify(
        process.env.API_URL || "http://127.0.0.1:8000"
      ),
    },
  },
});
