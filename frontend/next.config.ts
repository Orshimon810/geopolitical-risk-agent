import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for the Docker multi-stage build — emits a self-contained server
  // bundle that can be run with `node server.js` without node_modules.
  output: "standalone",
};

export default nextConfig;
