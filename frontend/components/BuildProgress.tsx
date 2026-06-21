"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BuildStatus } from "@/lib/types";

/* Step 3 (transient) — building: pacing plan + 3-track scripts + RAG index.
   Polls the backend's async build and advances automatically when done. */
export default function BuildProgress({
  jobId,
  onBuilt,
}: {
  jobId: string;
  onBuilt: () => void;
}) {
  const [status, setStatus] = useState<BuildStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.buildStatus(jobId);
        if (!alive) return;
        setStatus(s);
        if (s.state === "done") {
          setTimeout(onBuilt, 500);
          return;
        }
        if (s.state === "error") {
          setError(s.error || "The build failed.");
          return;
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Lost contact with the build.");
        return;
      }
      if (alive) setTimeout(tick, 400);
    };
    tick();
    return () => {
      alive = false;
    };
  }, [jobId, onBuilt]);

  const total = status?.total ?? 0;
  const done = status?.slides_done ?? 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 6;

  return (
    <div className="rise mx-auto w-full max-w-2xl text-center">
      <p className="eyebrow mb-3">Building</p>
      <h2 className="font-display text-3xl font-semibold tracking-tight">
        Scripting and timing your deck
      </h2>
      <p className="mt-2 text-muted">
        Writing three pacing tracks and indexing every slide so it can answer questions.
      </p>

      <div className="mt-10">
        <div className="mb-2 flex items-center justify-between font-mono text-sm">
          <span className="text-muted">slides scripted</span>
          <span className="text-speak tabular-nums">
            {done} / {total || "…"}
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-hairline">
          <div
            className="h-full rounded-full bg-speak transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <div className="mt-8 flex justify-center gap-2">
        {(["STANDARD", "SUMMARY", "TURBO"] as const).map((tk) => {
          const built = status?.tracks_built?.includes(tk);
          return (
            <span
              key={tk}
              className={`rounded-full border px-3 py-1 font-mono text-xs ${
                built ? "border-listen text-listen" : "border-hairline text-faint"
              }`}
            >
              {built ? "✓ " : ""}
              {tk}
            </span>
          );
        })}
      </div>

      {error && (
        <p className="mt-6 text-sm text-over" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
