import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Repositories",
  description: "Connect and monitor the GitHub repositories running through Robot CI.",
};

export default function RepositoriesLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
