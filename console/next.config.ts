import type { NextConfig } from "next";

// Production builds are a static export copied into the Python package
// (src/syros/console/static/, gitignored); the Docker image builds it in a
// Node stage. `next dev` instead proxies /api to a locally running
// `syros console` (rewrites don't exist in a static export).
const nextConfig: NextConfig =
  process.env.NODE_ENV === "development"
    ? {
        rewrites: async () => [
          { source: "/api/:path*", destination: "http://127.0.0.1:8484/api/:path*" },
        ],
      }
    : { output: "export" };

export default nextConfig;
