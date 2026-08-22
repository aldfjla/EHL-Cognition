"use client";

/**
 * The hero's 3D scenario stack — six randomized worlds, on anime.js.
 *
 * A real perspective scene rather than a flat mock: the tiles fly in along Z
 * out of a rotated plane, keep a slow depth float afterwards, and the whole
 * scene tilts toward the pointer, so the parallax between near and far tiles is
 * the actual thing selling "N worlds at once".
 *
 * Two nested transforms on purpose — the wrapper owns the intro (`translateZ`,
 * `rotateX`) and the inner face owns the loop, because anime.js drives one
 * transform per element and the intro and the float would otherwise fight over
 * `translateZ`.
 */

import { animate, stagger, utils } from "animejs";
import clsx from "clsx";
import { useEffect, useRef } from "react";

interface Tile {
  seed: number;
  label: string;
  status: "passed" | "failed" | "running";
}

const TILES: Tile[] = [
  { seed: 4400, label: "light · high friction", status: "passed" },
  { seed: 4407, label: "medium · high friction", status: "passed" },
  { seed: 4414, label: "heavy · high friction", status: "running" },
  { seed: 4421, label: "light · nominal", status: "failed" },
  { seed: 4428, label: "medium · nominal", status: "passed" },
  { seed: 4435, label: "heavy · low friction", status: "passed" },
];

const STATUS_TONE: Record<Tile["status"], string> = {
  passed: "text-status-passed border-status-passed/40",
  failed: "text-status-failed border-status-failed/60",
  running: "text-status-running border-status-running/50",
};

/** Resting tilt: enough to read as a plane in space, not enough to distort. */
const REST = { rotateX: 9, rotateY: -14 };

export default function SimStack() {
  const rootRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    const scene = sceneRef.current;
    if (root === null || scene === null) return;

    const cards = Array.from(scene.querySelectorAll<HTMLElement>("[data-card]"));
    const faces = Array.from(scene.querySelectorAll<HTMLElement>("[data-face]"));

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      utils.set(cards, { opacity: 1 });
      return;
    }

    utils.set(scene, REST);
    animate(cards, {
      opacity: [0, 1],
      translateZ: [-320, 0],
      translateY: [90, 0],
      rotateX: [-42, 0],
      duration: 1200,
      delay: stagger(90, { start: 120 }),
      ease: "outExpo",
    });
    animate(faces, {
      translateZ: [0, 22],
      duration: 3200,
      delay: stagger(180),
      ease: "inOutSine",
      loop: true,
      alternate: true,
    });

    const onPointerMove = (event: PointerEvent): void => {
      const rect = root.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      animate(scene, {
        rotateY: REST.rotateY + x * 26,
        rotateX: REST.rotateX - y * 20,
        duration: 500,
        ease: "outQuad",
      });
    };

    const onPointerLeave = (): void => {
      animate(scene, { ...REST, duration: 1400, ease: "outElastic(1, 0.55)" });
    };

    root.addEventListener("pointermove", onPointerMove);
    root.addEventListener("pointerleave", onPointerLeave);
    return () => {
      root.removeEventListener("pointermove", onPointerMove);
      root.removeEventListener("pointerleave", onPointerLeave);
      utils.remove(cards);
      utils.remove(faces);
      utils.remove(scene);
    };
  }, []);

  return (
    <div
      ref={rootRef}
      aria-hidden
      className="relative select-none [perspective:1200px]"
    >
      <div
        ref={sceneRef}
        className="grid grid-cols-2 gap-3 [transform-style:preserve-3d] sm:grid-cols-3"
      >
        {TILES.map((tile) => (
          <div
            key={tile.seed}
            data-card
            className="opacity-0 [transform-style:preserve-3d]"
          >
            <div
              data-face
              className={clsx(
                "rounded-xl border bg-surface-raised/70 p-3 shadow-[0_24px_60px_-30px_rgba(0,0,0,0.9)] backdrop-blur",
                STATUS_TONE[tile.status],
              )}
            >
              <div className="aspect-video rounded-md bg-gradient-to-br from-slate-900 to-slate-950 ring-1 ring-inset ring-white/5" />
              <div className="mt-2 font-mono text-[9px] uppercase tracking-widest">
                {tile.status}
              </div>
              <div className="font-mono text-[9px] text-slate-500">
                seed {tile.seed}
              </div>
              <div className="truncate text-[10px] text-slate-400">
                {tile.label}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
