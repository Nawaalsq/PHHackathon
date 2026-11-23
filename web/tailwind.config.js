/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      // Color scales resolved through CSS variables (RGB channel triplets, so
      // opacity modifiers like bg-zinc-900/40 keep working). Values live in
      // index.css — re-theme the app there without touching page classes.
      colors: {
        zinc: {
          100: "rgb(var(--c-zinc-100) / <alpha-value>)",
          200: "rgb(var(--c-zinc-200) / <alpha-value>)",
          300: "rgb(var(--c-zinc-300) / <alpha-value>)",
          400: "rgb(var(--c-zinc-400) / <alpha-value>)",
          500: "rgb(var(--c-zinc-500) / <alpha-value>)",
          600: "rgb(var(--c-zinc-600) / <alpha-value>)",
          700: "rgb(var(--c-zinc-700) / <alpha-value>)",
          800: "rgb(var(--c-zinc-800) / <alpha-value>)",
          900: "rgb(var(--c-zinc-900) / <alpha-value>)",
          950: "rgb(var(--c-zinc-950) / <alpha-value>)",
        },
        sky: {
          300: "rgb(var(--c-sky-300) / <alpha-value>)",
          400: "rgb(var(--c-sky-400) / <alpha-value>)",
        },
        emerald: {
          300: "rgb(var(--c-emerald-300) / <alpha-value>)",
        },
        amber: {
          300: "rgb(var(--c-amber-300) / <alpha-value>)",
        },
        red: {
          300: "rgb(var(--c-red-300) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
};
