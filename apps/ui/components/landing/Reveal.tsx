"use client";

/**
 * Scroll-reveal wrapper for the landing page, on motion's `whileInView`.
 *
 * Reveals once and never re-animates on the way back up. The reveal is
 * decoration, so it must never be able to swallow the copy: the hidden state is
 * inline style, which the `noscript` rule in the root layout overrides, and
 * `prefers-reduced-motion` renders a plain div with no transform at all.
 */

import { motion, useReducedMotion } from "motion/react";

interface RevealProps {
  children: React.ReactNode;
  /** Stagger, in milliseconds, applied once the element enters the viewport. */
  delay?: number;
  className?: string;
}

/** Sharp start, long settle — the same curve as the tile pop on the wall. */
const EASE = [0.16, 1, 0.3, 1] as const;

export default function Reveal({ children, delay = 0, className }: RevealProps) {
  const reduceMotion = useReducedMotion();

  if (reduceMotion === true) {
    return <div className={className}>{children}</div>;
  }

  return (
    <motion.div
      data-reveal
      className={className}
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-12% 0px -12% 0px" }}
      transition={{ duration: 0.7, delay: delay / 1000, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}
