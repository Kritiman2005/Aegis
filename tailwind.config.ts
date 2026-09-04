import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Aegis Design System — light content on a warm-neutral ground, with a
        // coral/orange brand accent. The Sidebar stays dark by design (its own
        // aegis-sidebar-* tokens below), matching the reference's hybrid layout.
        aegis: {
          base: '#F4F4F5',
          // Translucent — paired with backdrop-blur (see globals.css) for the
          // frosted-glass card look. Needs soft color behind it to actually
          // read as "glass" rather than plain white — see the ambient
          // gradient blobs on <body> in globals.css.
          surface: 'rgba(255,255,255,0.65)',
          raised: 'rgba(255,255,255,0.72)',
          // Solid, not translucent: overlay is used for nested fills inside an
          // already-glass card (inputs, hover states, badges) — several call
          // sites apply Tailwind opacity modifiers (bg-aegis-overlay/50), which
          // replace rather than combine with a color's own alpha, so a
          // translucent base here produced much darker results than intended.
          overlay: '#F1F1F2',
          border: 'rgba(228,228,231,0.8)',
          'border-glow': 'rgba(244,98,45,0.25)',
          primary: '#F4622D',
          'primary-light': '#FF8B5E',
          'primary-dark': '#D14A1D',
          accent: '#FBBF24',
          'text-primary': '#18181B',
          'text-secondary': '#52525B',
          'text-muted': '#A1A1AA',
          success: '#16A34A',
          error: '#DC2626',
          warning: '#D97706',

          // Dark sidebar rail — intentionally its own palette, independent of
          // the light content tokens above. Translucent + backdrop-blur (see
          // globals.css) so the warm ambient glow on the page background
          // (which sits directly behind it — same bg-aegis-base wrapper the
          // Sidebar is a flex child of) bleeds through softly instead of the
          // panel reading as flat, dead black.
          sidebar: 'rgba(15,15,18,0.86)',
          'sidebar-raised': 'rgba(255,255,255,0.06)',
          'sidebar-border': 'rgba(255,255,255,0.08)',
          'sidebar-text': '#D4D4D8',
          'sidebar-text-muted': '#71717A',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      keyframes: {
        'fade-in-up': {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'typing-dot': {
          '0%, 60%, 100%': { transform: 'translateY(0)' },
          '30%': { transform: 'translateY(-5px)' },
        },
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 4px #F4622D' },
          '50%': { boxShadow: '0 0 16px #F4622D, 0 0 32px rgba(244,98,45,0.25)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% center' },
          '100%': { backgroundPosition: '200% center' },
        },
        'slide-in': {
          from: { opacity: '0', transform: 'translateX(-12px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'block-pop': {
          '0%': { opacity: '0', transform: 'scale(0.4)' },
          '60%': { opacity: '1', transform: 'scale(1.08)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
      },
      animation: {
        'fade-in-up': 'fade-in-up 0.25s ease-out forwards',
        'typing-dot': 'typing-dot 1.2s infinite ease-in-out',
        'pulse-glow': 'pulse-glow 2s ease-in-out infinite',
        shimmer: 'shimmer 2s linear infinite',
        'slide-in': 'slide-in 0.2s ease-out forwards',
        'fade-in': 'fade-in 0.3s ease-in-out',
        'block-pop': 'block-pop 0.9s cubic-bezier(0.34,1.56,0.64,1) both',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};

export default config;
