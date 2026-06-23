"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ToastProvider } from "@/components/Toast";
import Uploader from "@/components/Uploader";
import Configurator from "@/components/Configurator";
import BuildProgress from "@/components/BuildProgress";
import Presenter from "@/components/Presenter";

type Phase = "upload" | "configure" | "building" | "present";

const STEPS: { key: Phase; label: string }[] = [
  { key: "upload", label: "Deck" },
  { key: "configure", label: "Delivery" },
  { key: "present", label: "Present" },
];

export default function Home() {
  const [phase, setPhase] = useState<Phase>("upload");
  const [jobId, setJobId] = useState("");
  const [slideCount, setSlideCount] = useState(0);
  const [fileName, setFileName] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);

  // Show backend connectivity so a misconfigured API base is obvious immediately.
  useEffect(() => {
    let alive = true;
    const ping = async () => {
      const ok = await api.health();
      if (alive) setOnline(ok);
    };
    ping();
    const id = setInterval(ping, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Map the 4 internal phases onto the 3 visible steps.
  const activeStep: Phase =
    phase === "building" ? "present" : phase === "configure" ? "configure" : phase;

  function reset() {
    setPhase("upload");
    setJobId("");
    setSlideCount(0);
    setFileName("");
  }

  return (
    <ToastProvider>
      {/* Whole app fits one viewport — the page itself never scrolls. */}
      <div className="flex h-dvh flex-col overflow-hidden">
        {/* ---------------- header ---------------- */}
        <header className="shrink-0 border-b border-hairline bg-ground/80 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3">
            <button onClick={reset} className="flex items-center gap-2.5" aria-label="Chatterbot-AI home">
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-speak text-ground">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path d="M4 14c0 0 2-1 4 1s4 2 6-1 4-2 6 0" strokeLinecap="round" />
                  <path d="M4 9c0 0 2-1 4 1s4 2 6-1 4-2 6 0" strokeLinecap="round" opacity="0.5" />
                </svg>
              </span>
              <span className="font-display text-lg font-bold tracking-tight">Chatterbot</span>
            </button>

            {/* step indicator — only meaningful while there is a sequence to show */}
            <nav className="hidden items-center gap-1 sm:flex" aria-label="Progress">
              {STEPS.map((s, i) => {
                const active = s.key === activeStep;
                const idx = STEPS.findIndex((x) => x.key === activeStep);
                const done = i < idx;
                return (
                  <span key={s.key} className="flex items-center gap-1">
                    <span
                      className={`font-mono text-xs ${
                        active ? "text-speak" : done ? "text-listen" : "text-faint"
                      }`}
                    >
                      {String(i + 1).padStart(2, "0")} {s.label}
                    </span>
                    {i < STEPS.length - 1 && <span className="px-2 text-faint">·</span>}
                  </span>
                );
              })}
            </nav>
          </div>
        </header>

        {/* ---------------- stage (fills remaining height; no page scroll) ---------------- */}
        <main className="relative min-h-0 flex-1 overflow-hidden">
          <div className="mx-auto flex h-full max-w-6xl flex-col px-5 py-5">
            {phase === "upload" && (
              <div className="flex h-full flex-col items-center justify-center gap-8 text-center">
                <div>
                  <p className="eyebrow mb-3">Real-time voice presentations</p>
                  <h1 className="mx-auto max-w-3xl font-display text-3xl font-bold leading-[1.1] tracking-tight sm:text-5xl">
                    Deliver any deck
                    <span className="text-speak"> to the second.</span>
                    <br />
                    Interrupt it <span className="text-listen">anytime.</span>
                  </h1>
                  <p className="mx-auto mt-4 max-w-xl text-muted">
                    Chatterbot narrates your slides inside a strict time budget. Ask a question
                    with your voice and it answers from the deck — then resumes the exact sentence
                    it left off on.
                  </p>
                </div>
                <Uploader
                  onUploaded={(id, count, name) => {
                    setJobId(id);
                    setSlideCount(count);
                    setFileName(name);
                    setPhase("configure");
                  }}
                />
              </div>
            )}

            {phase === "configure" && (
              <div className="flex h-full flex-col justify-center">
                <Configurator
                  jobId={jobId}
                  slideCount={slideCount}
                  fileName={fileName}
                  onBuilding={() => setPhase("building")}
                />
              </div>
            )}

            {phase === "building" && (
              <div className="flex h-full flex-col justify-center">
                <BuildProgress jobId={jobId} onBuilt={() => setPhase("present")} />
              </div>
            )}

            {phase === "present" && (
              <div className="h-full min-h-0">
                <Presenter jobId={jobId} slideCount={slideCount} />
              </div>
            )}
          </div>
        </main>

        {/* ---------------- backend status: red/green button, bottom-right ---------------- */}
        <button
          onClick={() => api.health().then(setOnline)}
          title={online === null ? "Checking backend…" : online ? "Backend online" : "Backend offline — click to retry"}
          aria-label={online === null ? "Checking backend" : online ? "Backend online" : "Backend offline"}
          className={`fixed bottom-4 right-4 z-30 h-4 w-4 rounded-full ring-2 ring-ground transition-colors ${
            online === null
              ? "bg-faint"
              : online
                ? "bg-listen shadow-[0_0_12px_var(--listen-soft)]"
                : "bg-over shadow-[0_0_12px_var(--speak-soft)] animate-pulse"
          }`}
        />
      </div>
    </ToastProvider>
  );
}
