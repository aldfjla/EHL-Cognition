"use client";

/**
 * Scroll-reveal wrapper for the landing page.
 *
 * One IntersectionObserver per element, disconnected on first intersection so
 * nothing re-animates on the way back up. Content is rendered regardless of
 * observer support — the animation is decoration, never a gate on the copy.
 */

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

interface RevealProps {
  children: React.ReactNode;
  /** Stagger, in milliseconds, applied once the element enters the viewport. */
  delay?: number;
  className?: string;
}

export default function Reveal({ children, delay = 0, className }: RevealProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (node === null) return;
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setShown(true);
        observer.disconnect();
      },
      { rootMargin: "-12% 0px -12% 0px" },
    );
    observer.observe(node);

    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      style={shown && delay > 0 ? { transitionDelay: `${delay}ms` } : undefined}
      className={clsx(
        "transition-[opacity,transform] duration-700 ease-out motion-reduce:transition-none",
        shown ? "translate-y-0 opacity-100" : "translate-y-6 opacity-0",
        className,
      )}
    >
      {children}
    </div>
  );
}
