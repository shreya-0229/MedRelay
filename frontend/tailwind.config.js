/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        relay: {
          bg: "#f4f7fb",
          panel: "#ffffff",
          panel2: "#eef2f7",
          border: "#dbe3ec",
          ink: "#0f172a",
          navy: "#0b1f3a",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,0.05), 0 10px 28px -14px rgba(15,23,42,0.14)",
        "card-hover":
          "0 2px 4px rgba(15,23,42,0.06), 0 18px 38px -14px rgba(15,23,42,0.20)",
        pop: "0 4px 10px -2px rgba(37,99,235,0.35), 0 2px 4px rgba(15,23,42,0.08)",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Inter",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
