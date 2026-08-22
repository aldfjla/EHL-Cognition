import type { Metadata } from "next";
import "./globals.css";

import AppShell from "@/components/shell/AppShell";

export const metadata: Metadata = {
  title: "Robot CI",
  description:
    "Autonomous CI for robot control code — tested in simulation, fixed by agents.",
};

/**
 * Root layout: one slim shared bar (navigation, ⌘K palette, shortcut sheet).
 * Everything else belongs to the page — mission control owns its own grid.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        {/* Scroll reveals start hidden; without scripting they would never be
            told to appear. */}
        <noscript>
          <style>{"[data-reveal]{opacity:1!important;transform:none!important}"}</style>
        </noscript>
      </head>
      <body className="min-h-screen">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
