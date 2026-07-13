import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Aegis — Local AI Agent Platform',
  description:
    'Aegis is a local-first, privacy-focused AI agent platform. All intelligence runs on your machine.',
  keywords: ['AI', 'local LLM', 'agent', 'privacy', 'desktop'],
  authors: [{ name: 'Aegis Team' }],
  robots: 'noindex, nofollow', // Desktop app — no indexing needed
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#080B14" />
        {/* Preconnect to Google Fonts for performance */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
      </head>
      <body className="h-full bg-[#080B14] text-[#F1F5F9] antialiased">
        {children}
      </body>
    </html>
  );
}
