/** @type {import('next').NextConfig} */
const nextConfig = {
  // Use static HTML export only when building for production Electron package
  output: process.env.NEXT_EXPORT ? 'export' : undefined,

  // Disable built-in image optimization for local desktop compatibility
  images: {
    unoptimized: true,
  },

  // Trailing slash for consistent resolution
  trailingSlash: true,
};

module.exports = nextConfig;
