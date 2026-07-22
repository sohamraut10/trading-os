import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fable — Financial Intelligence",
  description: "Voice-first personal finance intelligence."
};

export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}