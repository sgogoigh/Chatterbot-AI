"use client";

import { useEffect, useRef } from "react";
import type { SessionPhase } from "@/lib/types";

/*
  VoiceOrb — the present screen's hero. A circular "voiceprint" rendered on Canvas
  whose color + motion encode the agent's full-duplex state:
    SPEAKING / ANSWERING  -> amber, tall radiating bars (on air)
    LISTENING             -> teal, low shimmering ring (ear open)
    PROCESSING            -> teal, a rotating highlight (thinking)
    READY / PAUSED / ...  -> muted, gentle breathing
  This is the single orchestrated motion moment; everything else stays quiet.
*/

const SPEAK = "#f2a33c";
const LISTEN = "#35c9c9";
const MUTED = "#5b636f";

function colorFor(phase: SessionPhase): string {
  if (phase === "SPEAKING" || phase === "ANSWERING") return SPEAK;
  if (phase === "LISTENING" || phase === "PROCESSING") return LISTEN;
  return MUTED;
}

export default function VoiceOrb({ phase }: { phase: SessionPhase }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef<SessionPhase>(phase);
  phaseRef.current = phase;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const size = 280;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);
    const cx = size / 2;
    const cy = size / 2;
    const BARS = 72;

    let raf = 0;
    let t = 0;

    // smooth pseudo-random energy per bar, varying by phase intensity
    const energy = (i: number, time: number, intensity: number) => {
      const a = Math.sin(i * 0.5 + time * 2.1) * 0.5 + 0.5;
      const b = Math.sin(i * 1.3 - time * 1.4) * 0.5 + 0.5;
      return (a * 0.6 + b * 0.4) * intensity;
    };

    const draw = () => {
      const phase = phaseRef.current;
      const color = colorFor(phase);
      const speaking = phase === "SPEAKING" || phase === "ANSWERING";
      const listening = phase === "LISTENING";
      const processing = phase === "PROCESSING";
      const intensity = speaking ? 1 : listening ? 0.45 : processing ? 0.3 : 0.18;
      const baseR = 64;

      ctx.clearRect(0, 0, size, size);

      // soft glow core
      const glow = ctx.createRadialGradient(cx, cy, 8, cx, cy, baseR + 36);
      glow.addColorStop(0, color + "55");
      glow.addColorStop(1, color + "00");
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR + 36, 0, Math.PI * 2);
      ctx.fill();

      // breathing core ring
      const breathe = reduce ? 0 : Math.sin(t * 1.6) * 3;
      ctx.strokeStyle = color + "88";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR - 10 + breathe, 0, Math.PI * 2);
      ctx.stroke();

      // circular voiceprint bars
      for (let i = 0; i < BARS; i++) {
        const ang = (i / BARS) * Math.PI * 2 - Math.PI / 2;
        const e = reduce ? intensity * 0.5 : energy(i, t, intensity);
        const len = 6 + e * 46;
        const r0 = baseR;
        const r1 = baseR + len;
        const x0 = cx + Math.cos(ang) * r0;
        const y0 = cy + Math.sin(ang) * r0;
        const x1 = cx + Math.cos(ang) * r1;
        const y1 = cy + Math.sin(ang) * r1;
        ctx.strokeStyle = color + "cc";
        ctx.lineWidth = 2.4;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.stroke();
      }

      // processing: a bright rotating arc
      if (processing && !reduce) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.arc(cx, cy, baseR + 26, t * 3, t * 3 + Math.PI * 0.5);
        ctx.stroke();
      }

      if (!reduce) {
        t += 0.016;
        raf = requestAnimationFrame(draw);
      }
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: 280, height: 280 }}
      role="img"
      aria-label={`Agent state: ${phase.toLowerCase()}`}
    />
  );
}
