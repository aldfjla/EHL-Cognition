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
      keyframes: {
        // A fan-out of six agents has to read as a fan-out, not a snap.
        rise: {
          "0%": { opacity: "0", transform: "translateY(6px) scale(0.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        // A cell landing red should be noticed from across the room.
        land: {
          "0%": { transform: "scale(0.6)", opacity: "0.2" },
          "60%": { transform: "scale(1.12)" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
      },
      animation: {
        rise: "rise 260ms ease-out",
        land: "land 320ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
