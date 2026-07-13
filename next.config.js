/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static HTML export — required for Electron production loading via loadFile()
  output: 'export',

  // Disable built-in image optimization (not compatible with static export)
  images: {
    unoptimized: true,
  },

  // Trailing slash for consistent static file resolution in Electron
  trailingSlash: true,

  // Source directory is src/
  // (Next.js auto-detects this when src/app/ exists)
};

module.exports = nextConfig;
