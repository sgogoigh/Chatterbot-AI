import type { Metadata } from "next";
import { Space_Grotesk, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// Display: Space Grotesk — geometric with quirks, the product's voice.
const display = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});
// Body/UI: Inter — quiet, legible workhorse.
const sans = Inter({ variable: "--font-sans", subsets: ["latin"] });
// Data: JetBrains Mono — carries the clock, time budget, and latency figures.
const mono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "700"],
});

export const metadata: Metadata = {
  title: "Chatterbot-AI — Voice presentations, to the second",
  description:
    "Upload a deck and let Chatterbot deliver it in exactly your time budget. "
    + "Interrupt with your voice anytime — it answers, then resumes the exact sentence.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${sans.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="min-h-full">{children}</body>
    </html>
  );
}
