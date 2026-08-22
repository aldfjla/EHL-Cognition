"use client";

/**
 * Lenis smooth scrolling, mounted once for the landing page.
 *
 * Lenis drives the real document scroll position (it is not a virtual
 * container), so everything that reads `scrollY` — the progress hairline,
 * motion's `useScroll`, `IntersectionObserver` — keeps working untouched.
 *
 * Two things it does not handle for us:
 *   - in-page anchors, because the browser's instant jump bypasses the eased
 *     scroll entirely; we intercept those clicks and hand them to Lenis with
 *     the sticky header's height as an offset;
 *   - `prefers-reduced-motion`, where hijacking the wheel is exactly what the
 *     user asked us not to do — there we never instantiate it.
 */

import Lenis from "lenis";
import { useEffect } from "react";

import "lenis/dist/lenis.css";

/** Height of the sticky header, so anchored sections don't land under it. */
const HEADER_OFFSET = 72;

function isPlainLeftClick(event: MouseEvent): boolean {
  return (
    !event.defaultPrevented &&
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}

export default function SmoothScroll() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const lenis = new Lenis({
      duration: 1.1,
      wheelMultiplier: 1,
      touchMultiplier: 1.8,
      // A long page of text: easing out of the wheel impulse reads as weight,
      // not as lag.
      easing: (t: number) => 1 - Math.pow(1 - t, 3),
    });

    let frame = requestAnimationFrame(function raf(time: number) {
      lenis.raf(time);
      frame = requestAnimationFrame(raf);
    });

    const onClick = (event: MouseEvent): void => {
      if (!isPlainLeftClick(event)) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest('a[href^="#"]');
      if (!(anchor instanceof HTMLAnchorElement)) return;
      const id = anchor.hash.slice(1);
      const section = id === "" ? null : document.getElementById(id);
      if (section === null) return;
      event.preventDefault();
      lenis.scrollTo(section, { offset: -HEADER_OFFSET });
      window.history.replaceState(null, "", anchor.hash);
    };

    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("click", onClick);
      cancelAnimationFrame(frame);
      lenis.destroy();
    };
  }, []);

  return null;
}
