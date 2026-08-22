import type { NextConfig } from "next";

/**
 * The dashboard is a pure client of the FastAPI process; it owns no data.
 * API and WebSocket origins come from the environment so the same build runs
 * against a local API and (later) a tunnelled one during the demo.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
    NEXT_PUBLIC_WS_BASE: process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000",
  },
  // TODO(build): add a rewrite proxying /api/* to the FastAPI process so the
  // dashboard can be served from one origin and CORS stops mattering.
};

export default nextConfig;
