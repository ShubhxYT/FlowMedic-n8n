import type { Metadata } from "next";

import "@fontsource-variable/manrope/wght.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "./globals.css";
import "./dashboard.css";

export const metadata: Metadata = {
  title: "FlowMedic AI | Automation observability",
  description: "Observe and diagnose failed automation workflows.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
