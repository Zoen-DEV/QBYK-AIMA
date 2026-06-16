/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f4ff",
          100: "#dce8ff",
          500: "#4f6ef7",
          600: "#3b56e8",
          700: "#2d43d0",
        },
      },
      keyframes: {
        // Brillo que recorre una barra/skeleton para comunicar "trabajo en curso".
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        // Barra indeterminada: barre de lado a lado cuando no hay progreso medible.
        indeterminate: {
          "0%": { transform: "translateX(-120%)" },
          "100%": { transform: "translateX(320%)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.6s infinite",
        indeterminate: "indeterminate 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
