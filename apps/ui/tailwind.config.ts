import type { Config } from "tailwindcss";

/**
 * Mission-control palette: dark by default, one accent per pipeline outcome.
 * Status colours are named by meaning, not by hue, so a component never has to
 * decide what "failed" looks like.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0b0f14",
          raised: "#131a22",
          border: "#1f2a35",
        },
        status: {
          pending: "#5b6b7c",
          running: "#38bdf8",
          passed: "#34d399",
          failed: "#f87171",
          error: "#fbbf24",
          blocked: "#c084fc",
        },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
