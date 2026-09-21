/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the production Docker image small — it traces
  // and copies only the node_modules a running server actually needs.
  output: "standalone",
  // Node 24 + Next 14's jest-worker child-process pool crashes with
  // EPIPE on Windows when workers get recycled. Single worker avoids
  // the recycling path that triggers it.
  experimental: {
    cpus: 1,
  },
};

export default nextConfig;
