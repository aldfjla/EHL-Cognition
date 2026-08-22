"use client";

/**
 * Hairline scroll indicator pinned to the top of the landing page.
 *
 * Reads scroll position on a rAF-throttled listener and drives a scaleX, so the
 * bar never triggers layout while the page moves.
 */

import { useEffect, useState } from "react";

export default function ScrollProgress() {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let frame: number | null = null;

    const measure = (): void => {
      frame = null;
      const doc = document.documentElement;
      const scrollable = doc.scrollHeight - doc.clientHeight;
      setProgress(scrollable <= 0 ? 0 : Math.min(1, doc.scrollTop / scrollable));
    };

    const onScroll = (): void => {
      if (frame !== null) return;
      frame = requestAnimationFrame(measure);
    };

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);

    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return (
    <div className="fixed inset-x-0 top-0 z-50 h-px bg-transparent">
      <div
        className="h-px origin-left bg-gradient-to-r from-sky-400 to-emerald-300"
        style={{ transform: `scaleX(${progress})` }}
      />
    </div>
  );
}
