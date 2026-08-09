/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Node 24 + Next 14's jest-worker child-process pool crashes with
  // EPIPE on Windows when workers get recycled. Single worker avoids
  // the recycling path that triggers it.
  experimental: {
    cpus: 1,
  },
};

export default nextConfig;
