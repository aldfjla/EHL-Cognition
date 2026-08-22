import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Harness",
  description: "Replay the Robot CI agent operations panel without a live run.",
};

export default function AgentsDemoLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
