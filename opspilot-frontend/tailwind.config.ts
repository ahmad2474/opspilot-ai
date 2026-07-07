import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Ops-console palette — deliberately not the templated
        // cream/serif or near-black/neon-vermillion defaults.
        bg: "#0B0F14", // page background — near-black slate, not pure black
        surface: "#111820", // card/panel surface
        surfacealt: "#151D27", // slightly raised surface (input fields, hover)
        border: "#1F2A36",
        text: "#E5E9EE",
        muted: "#8B96A5",
        accent: "#F0A202", // warm amber — ops/alert-lamp signal, not AWS orange
        "status-good": "#3FB950",
        "status-bad": "#F85149",
        "status-neutral": "#6E7681",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-jbmono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};

export default config;
