"use client";

/**
 * Hairline scroll indicator pinned to the top of the landing page.
 *
 * `useScroll` gives a 0→1 document progress off motion's own frame loop, and a
 * spring keeps the bar from stuttering behind Lenis' eased scroll. Only scaleX
 * is animated, so the bar never triggers layout while the page moves.
 */

import { motion, useScroll, useSpring } from "motion/react";

export default function ScrollProgress() {
  const { scrollYProgress } = useScroll();
  const scaleX = useSpring(scrollYProgress, {
    stiffness: 260,
    damping: 40,
    restDelta: 0.001,
  });

  return (
    <div className="fixed inset-x-0 top-0 z-50 h-px bg-transparent">
      <motion.div
        className="h-px origin-left bg-gradient-to-r from-sky-400 to-emerald-300"
        style={{ scaleX }}
      />
    </div>
  );
}
