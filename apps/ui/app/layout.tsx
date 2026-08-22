import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Robot CI",
  description:
    "Autonomous CI for robot control code — tested in simulation, fixed by agents.",
};

/**
 * Root layout. Deliberately minimal: the mission control page owns its own
 * grid, and a shared chrome would only fight it for vertical space.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
