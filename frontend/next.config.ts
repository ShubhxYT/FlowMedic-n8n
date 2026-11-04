import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The dev server only trusts the hostname it starts on (localhost) plus
  // `localhost` subdomains. `127.0.0.1` is a different hostname, so without
  // this entry Next blocks the dev client + HMR and the app never hydrates.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
