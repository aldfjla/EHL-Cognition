import type { Metadata } from "next";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ runId: string }>;
}): Promise<Metadata> {
  const { runId } = await params;
  return {
    title: `Run ${runId}`,
    description: "Mission control for this Robot CI run.",
  };
}

export default function RunLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
