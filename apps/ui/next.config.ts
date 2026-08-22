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
  /**
   * Proxy `/api/*` to the FastAPI process so the dashboard can be served from
   * a single origin and CORS stops mattering — useful when the demo runs
   * through one tunnel.
   */
  async rewrites() {
    const target = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};

export default nextConfig;
