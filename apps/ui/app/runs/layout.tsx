import type { Metadata } from "next";

export const metadata: Metadata = {
  title: {
    default: "Runs",
    template: "%s · Robot CI",
  },
  description: "Browse every Robot CI run, newest first.",
};

export default function RunsLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
