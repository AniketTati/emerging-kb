import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Wave A: hide the X-Powered-By header (small attack-surface reduction).
  poweredByHeader: false,
};

export default nextConfig;
