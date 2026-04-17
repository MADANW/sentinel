import type { Metadata } from 'next'
import { JetBrains_Mono, DM_Sans } from 'next/font/google'
import './globals.css'

// JetBrains Mono — all numbers, prices, tickers, machine-generated values
const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
})

// DM Sans — labels, headers, prose
const dmSans = DM_Sans({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'algo-bot',
  description: 'Personal trading dashboard',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // dark class enforces dark mode unconditionally (trading tool, not consumer app)
    <html lang="en" className="dark">
      <body
        className={`${jetbrainsMono.variable} ${dmSans.variable} bg-gray-950 text-gray-100 font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  )
}
