import { defineConfig } from 'vitest/config';
import path from 'node:path';

// Pure-logic unit tests only for now (Node environment, no jsdom/React
// Testing Library) — mirrors tsconfig's "@/*" alias so tests can import
// from src/ the same way the app does. Component-rendering tests are a
// deliberately separate, bigger follow-up (needs jsdom + Testing Library,
// and this app's view components are large/stateful enough that testing
// them meaningfully is its own project, not a quick addition).
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
