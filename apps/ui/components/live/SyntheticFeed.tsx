"use client";

/**
 * A canvas-drawn stand-in feed for the scripted live replay.
 *
 * The mock has no worker rendering JPEGs, but a demo wall full of "no feed"
 * placeholders would not demonstrate the wall. This draws a small seeded
 * pick-and-place vignette — arm, cube, bin — advancing with `progress`, so the
 * replay looks alive while remaining visibly synthetic (it is labelled by the
 * replay banner, not by pretending to be video).
 *
 * Purely cosmetic and client-side; nothing here feeds back into any state.
 */

import { useEffect, useRef } from "react";

export interface SyntheticFeedProps {
  seed: number;
  /** 0..1 through the scenario. */
  progress: number;
  className?: string;
}

/** Small deterministic PRNG so each tile looks different but stable. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const W = 320;
const H = 180;

export default function SyntheticFeed({ seed, progress, className }: SyntheticFeedProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const rand = mulberry32(seed);
    const cubeX = 60 + rand() * 80;
    const binX = 220 + rand() * 40;
    const cubeSize = 14 + rand() * 8;
    const armBase = 40 + rand() * 30;

    ctx.fillStyle = "#0d1420";
    ctx.fillRect(0, 0, W, H);

    // Table.
    ctx.fillStyle = "#1c2836";
    ctx.fillRect(0, 140, W, 40);

    // Bin.
    ctx.strokeStyle = "#3b82f6";
    ctx.lineWidth = 2;
    ctx.strokeRect(binX, 118, 46, 22);

    // Cube travels from table to bin with progress.
    const t = Math.min(1, Math.max(0, progress));
    const carry = Math.min(1, Math.max(0, (t - 0.35) / 0.5));
    const x = cubeX + (binX + 16 - cubeX) * carry;
    const lift = Math.sin(Math.min(1, carry) * Math.PI) * 48;
    const y = 140 - cubeSize - lift;
    ctx.fillStyle = "#f59e0b";
    ctx.fillRect(x, y, cubeSize, cubeSize);

    // Two-segment arm tracking the cube.
    const shoulderX = armBase;
    const shoulderY = 30;
    const elbowX = (shoulderX + x) / 2;
    const elbowY = Math.min(shoulderY + 50, y - 18);
    ctx.strokeStyle = "#94a3b8";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(shoulderX, shoulderY);
    ctx.lineTo(elbowX, elbowY);
    ctx.lineTo(x + cubeSize / 2, y - 4);
    ctx.stroke();

    // Gripper.
    ctx.strokeStyle = "#e2e8f0";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - 2, y - 4);
    ctx.lineTo(x - 2, y + 6);
    ctx.moveTo(x + cubeSize + 2, y - 4);
    ctx.lineTo(x + cubeSize + 2, y + 6);
    ctx.stroke();

    // Sim clock, so consecutive frames visibly differ.
    ctx.fillStyle = "#475569";
    ctx.font = "10px ui-monospace, monospace";
    ctx.fillText(`t=${(t * 8).toFixed(2)}s`, 8, 14);
  }, [seed, progress]);

  return (
    <canvas
      ref={canvasRef}
      width={W}
      height={H}
      className={className}
      aria-label="synthetic replay feed"
    />
  );
}
