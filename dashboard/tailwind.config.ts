import type { Config } from 'tailwindcss'

const config: Config = {
  // Dark mode via 'class' strategy — html element gets className="dark"
  darkMode: 'class',
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // JetBrains Mono: all numbers, prices, tickers, machine-generated values
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        // DM Sans: labels, headers, prose
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

export default config
