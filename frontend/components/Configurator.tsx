"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useToast } from "./Toast";
import type { Persona } from "@/lib/types";

/* Step 2 — set the contract: how it should sound, and how long it must take. */
const PERSONAS: { id: Persona; name: string; blurb: string }[] = [
  { id: "GENERAL", name: "General", blurb: "Clear and neutral" },
  { id: "TEACHER", name: "Teacher", blurb: "Warm, explains step by step" },
  { id: "MEETING", name: "Meeting", blurb: "Concise and businesslike" },
  { id: "TEDX", name: "TEDx", blurb: "Vivid and narrative" },
];

export default function Configurator({
  jobId,
  slideCount,
  fileName,
  onBuilding,
}: {
  jobId: string;
  slideCount: number;
  fileName: string;
  onBuilding: () => void;
}) {
  const [persona, setPersona] = useState<Persona>("GENERAL");
  const [minutes, setMinutes] = useState(10);
  const [busy, setBusy] = useState(false);
  const notify = useToast();

  async function start() {
    setBusy(true);
    try {
      await api.build(jobId, minutes, persona);
      onBuilding();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't start the build.");
      setBusy(false);
    }
  }

  return (
    <div className="rise mx-auto w-full max-w-2xl">
      <p className="eyebrow mb-3">Step 2 — Delivery</p>
      <h2 className="font-display text-3xl font-semibold tracking-tight">Set the delivery</h2>
      <p className="mt-2 text-muted">
        <span className="font-mono text-text">{slideCount}</span> slides from{" "}
        <span className="text-text">{fileName}</span>. Choose a voice and a time budget —
        Chatterbot paces itself to land on the dot.
      </p>

      {/* Persona cards */}
      <fieldset className="mt-8">
        <legend className="eyebrow mb-3">Voice</legend>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {PERSONAS.map((p) => {
            const active = persona === p.id;
            return (
              <button
                key={p.id}
                onClick={() => setPersona(p.id)}
                aria-pressed={active}
                className={`rounded-xl border p-4 text-left transition-colors ${
                  active
                    ? "border-speak bg-speak/10"
                    : "border-hairline bg-panel hover:border-faint"
                }`}
              >
                <p className={`font-display font-semibold ${active ? "text-speak" : "text-text"}`}>
                  {p.name}
                </p>
                <p className="mt-1 text-xs text-muted">{p.blurb}</p>
              </button>
            );
          })}
        </div>
      </fieldset>

      {/* Duration dial */}
      <div className="mt-8">
        <div className="mb-3 flex items-baseline justify-between">
          <span className="eyebrow">Time budget</span>
          <span className="font-mono text-2xl font-medium text-speak tabular-nums">
            {minutes}:00
          </span>
        </div>
        <input
          type="range"
          min={1}
          max={45}
          value={minutes}
          onChange={(e) => setMinutes(Number(e.target.value))}
          className="w-full"
          aria-label="Time budget in minutes"
        />
        <div className="mt-2 flex justify-between font-mono text-[0.7rem] text-faint">
          <span>1 min</span>
          <span>45 min</span>
        </div>
      </div>

      <button
        onClick={start}
        disabled={busy}
        className="mt-10 w-full rounded-xl bg-speak px-6 py-3.5 font-display font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {busy ? "Starting…" : `Build the ${minutes}-minute delivery`}
      </button>
    </div>
  );
}
