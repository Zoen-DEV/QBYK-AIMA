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
    },
  },
  plugins: [],
};
