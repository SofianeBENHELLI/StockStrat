import type { NextConfig } from "next";

// The browser talks to this server only; /api/* is forwarded to the backend.
// Without it the page would call http://localhost:8001 from the *browser* —
// which, on a phone reaching the app over the network, is the phone itself.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
  // "Backtest everything" runs nine backtests in one request (~30 s).
  experimental: { proxyTimeout: 180_000 },
};

export default nextConfig;
