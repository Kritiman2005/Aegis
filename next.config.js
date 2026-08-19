/** @type {import('next').NextConfig} */
const nextConfig = {
  // Export as static HTML for Electron packaging in production
  output: process.env.NODE_ENV === 'production' ? 'export' : undefined,

  // Use relative paths for all static assets so they resolve correctly
  // under Electron's file:// protocol (absolute '/_next/...' breaks on Windows)
  assetPrefix: '.',

  // Disable built-in image optimization for local desktop compatibility
  images: {
    unoptimized: true,
  },

  // Trailing slash for consistent resolution
  trailingSlash: true,
};

module.exports = nextConfig;
