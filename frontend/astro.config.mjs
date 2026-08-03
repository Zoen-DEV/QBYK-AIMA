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
    // Una sola instancia de React, pase lo que pase. Si Vite re-optimiza las
    // dependencias en caliente (cambia el lockfile, se instala algo), la página
    // puede quedar con dos generaciones del prebundle vivas a la vez y cargar
    // React dos veces con URLs distintas. Ahí el dispatcher de hooks queda en
    // null y CUALQUIER isla React muere al hidratar con "Cannot read properties
    // of null (reading 'useState')" — sin isla no hay EventSource, y la pantalla
    // de progreso se queda cargando para siempre aunque el job ya haya terminado.
    resolve: { dedupe: ["react", "react-dom"] },
    optimizeDeps: { include: ["react", "react-dom", "react-dom/client"] },
  },
});
