import type { Metadata } from "next";

import { DashboardShell } from "@/components/dashboard-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "FlowMedic AI | Automation observability",
  description: "Observe and diagnose failed automation workflows.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><DashboardShell>{children}</DashboardShell></body></html>;
}
