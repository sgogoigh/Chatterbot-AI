"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { connectVoice, type VoiceConnection } from "@/lib/livekit";
import { useToast } from "./Toast";
import type { SlideContent, StartSessionResponse, StatusResponse } from "@/lib/types";
import VoiceOrb from "./VoiceOrb";

/* Step 4 — the stage. Slide viewer + voice orb + transport + time-budget rail.
   The displayed slide always follows the backend status (so voice-driven and
   button-driven navigation stay in sync). Voice is an enhancement: every control
   here works over REST even if the live mic connection isn't established. */

function clock(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const PHASE_LABEL: Record<string, { text: string; tone: "speak" | "listen" | "muted" }> = {
  SPEAKING: { text: "On air", tone: "speak" },
  ANSWERING: { text: "Answering", tone: "speak" },
  LISTENING: { text: "Listening", tone: "listen" },
  PROCESSING: { text: "Thinking", tone: "listen" },
  READY: { text: "Ready", tone: "muted" },
  PAUSED: { text: "Paused", tone: "muted" },
  TRANSITIONING: { text: "Moving", tone: "muted" },
  ENDED: { text: "Finished", tone: "muted" },
  IDLE: { text: "Idle", tone: "muted" },
  LOADING: { text: "Loading", tone: "muted" },
};

export default function Presenter({
  jobId,
  slideCount,
}: {
  jobId: string;
  slideCount: number;
}) {
  const [session, setSession] = useState<StartSessionResponse | null>(null);
  const [slides, setSlides] = useState<SlideContent[]>([]);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [voice, setVoice] = useState<"off" | "connecting" | "live" | "error">("off");
  const [voiceMsg, setVoiceMsg] = useState<string | null>(null);
  const [imgFailed, setImgFailed] = useState(false);
  const notify = useToast();

  const audioRef = useRef<HTMLAudioElement>(null);
  const connRef = useRef<VoiceConnection | null>(null);
  const startedRef = useRef(false);

  // Start a session + load slide metadata exactly once. The guard is essential:
  // without it, React StrictMode's double-invoked effect fires two POST /api/sessions,
  // and the backend's single-presentation rule keeps the second while we'd hold the
  // first — every later navigate/control then 404s ("unknown session").
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    (async () => {
      try {
        const [s, sl] = await Promise.all([api.startSession(jobId), api.slides(jobId)]);
        setSession(s);
        setSlides(sl);
      } catch (e) {
        notify(e instanceof Error ? e.message : "Couldn't start the session.");
      }
    })();
  }, [jobId, notify]);

  // Poll session status for phase / slide / drift.
  useEffect(() => {
    if (!session) return;
    let alive = true;
    const tick = async () => {
      try {
        const st = await api.status(session.session_id);
        if (alive) setStatus(st);
      } catch {
        /* transient; keep last status */
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [session]);

  // Disconnect voice on unmount.
  useEffect(() => () => void connRef.current?.disconnect(), []);

  // Release the session when the tab actually closes / navigates away / refreshes,
  // so it doesn't stay "active" and block the next presentation. `pagehide` (not a
  // React unmount) is used on purpose: it won't fire on StrictMode's simulated
  // unmount, so it can't kill a session we just created. Tab-switching does not
  // trigger pagehide, so switching away mid-talk leaves the session alive.
  useEffect(() => {
    if (!session) return;
    const sid = session.session_id;
    const onHide = () => api.stopBeacon(sid);
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [session]);

  const current = status?.current_slide ?? 0;
  const slide = slides[current];

  useEffect(() => setImgFailed(false), [current]);

  const nav = useCallback(
    async (intent: "NEXT" | "PREV") => {
      if (!session) return;
      try {
        setStatus(await api.navigate(session.session_id, intent));
      } catch (e) {
        notify(e instanceof Error ? e.message : "Navigation failed.");
      }
    },
    [session, notify],
  );

  const goto = useCallback(
    async (i: number) => {
      if (!session) return;
      try {
        setStatus(await api.navigate(session.session_id, "GOTO", i));
      } catch (e) {
        notify(e instanceof Error ? e.message : "Navigation failed.");
      }
    },
    [session, notify],
  );

  const control = useCallback(
    async (action: "pause" | "resume" | "stop") => {
      if (!session) return;
      try {
        setStatus(await api.control(session.session_id, action));
      } catch (e) {
        notify(e instanceof Error ? e.message : "Control failed.");
      }
    },
    [session, notify],
  );

  const toggleVoice = useCallback(async () => {
    if (voice === "live") {
      await connRef.current?.disconnect();
      connRef.current = null;
      setVoice("off");
      return;
    }
    if (!session || !audioRef.current) return;
    setVoice("connecting");
    setVoiceMsg(null);
    try {
      connRef.current = await connectVoice(session.livekit_url, session.token, audioRef.current);
      setVoice("live");
    } catch (e) {
      setVoice("error");
      setVoiceMsg(
        e instanceof Error ? e.message : "Couldn't connect the microphone. You can still drive it with the buttons below.",
      );
    }
  }, [voice, session]);

  const phase = status?.phase ?? "READY";
  const pl = PHASE_LABEL[phase] ?? PHASE_LABEL.READY;
  const paused = phase === "PAUSED";
  const drift = status?.drift_pct ?? 0;
  const overBudget = drift > 0.05;

  return (
    <div className="rise grid h-full min-h-0 w-full gap-5 lg:grid-cols-[1fr_320px]">
      {/* ---------------- stage ---------------- */}
      <section className="flex min-h-0 flex-col gap-3">
        <div className="panel relative min-h-0 w-full flex-1 overflow-hidden">
          {slide && !imgFailed ? (
            // Backend-rendered raster; falls back to a text card if unavailable.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={api.slideImage(jobId, current)}
              alt={slide.title || `Slide ${current + 1}`}
              className="h-full w-full object-contain bg-panel-2"
              onError={() => setImgFailed(true)}
            />
          ) : (
            <div className="flex h-full flex-col justify-center gap-4 p-10">
              <p className="eyebrow">Slide {current + 1}</p>
              <h3 className="font-display text-2xl font-semibold">
                {slide?.title || "Untitled slide"}
              </h3>
              <p className="max-w-prose text-muted line-clamp-6">
                {slide?.body_text || "No slide preview available."}
              </p>
            </div>
          )}
          <span className="absolute bottom-3 right-3 rounded-md bg-ground/70 px-2 py-1 font-mono text-xs text-muted backdrop-blur">
            {current + 1} / {slideCount}
          </span>
        </div>

        {/* transport */}
        <div className="panel flex flex-wrap items-center gap-2 p-3">
          <TransportBtn label="Previous slide" onClick={() => nav("PREV")} disabled={current <= 0}>
            <path d="M15 6l-6 6 6 6" />
          </TransportBtn>
          {paused ? (
            <button
              onClick={() => control("resume")}
              className="flex items-center gap-2 rounded-lg bg-speak px-4 py-2 font-medium text-ground hover:opacity-90"
            >
              <Icon><path d="M7 5v14l11-7z" fill="currentColor" stroke="none" /></Icon>
              Resume
            </button>
          ) : (
            <button
              onClick={() => control("pause")}
              className="flex items-center gap-2 rounded-lg border border-hairline px-4 py-2 font-medium hover:border-faint"
            >
              <Icon><path d="M7 5v14M17 5v14" /></Icon>
              Pause
            </button>
          )}
          <TransportBtn label="Next slide" onClick={() => nav("NEXT")} disabled={current >= slideCount - 1}>
            <path d="M9 6l6 6-6 6" />
          </TransportBtn>

          <div className="mx-1 h-6 w-px bg-hairline" />

          <button
            onClick={toggleVoice}
            aria-pressed={voice === "live"}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 font-medium transition-colors ${
              voice === "live"
                ? "bg-listen/15 text-listen ring-1 ring-listen"
                : "border border-hairline hover:border-faint"
            }`}
          >
            <Icon>
              <path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
            </Icon>
            {voice === "live" ? "Mic on" : voice === "connecting" ? "Connecting…" : "Go live"}
          </button>

          <button
            onClick={() => control("stop")}
            className="ml-auto flex items-center gap-2 rounded-lg border border-hairline px-4 py-2 font-medium text-over hover:border-over"
          >
            <Icon><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" /></Icon>
            End
          </button>
        </div>

        {voiceMsg && (
          <p className="text-sm text-muted" role="status">
            {voiceMsg}
          </p>
        )}

        {/* slide rail (GOTO by click) */}
        <div className="flex gap-2 overflow-x-auto pb-1">
          {Array.from({ length: slideCount }).map((_, i) => (
            <button
              key={i}
              onClick={() => goto(i)}
              aria-label={`Go to slide ${i + 1}`}
              aria-current={i === current}
              className={`h-9 min-w-9 shrink-0 rounded-lg border px-2 font-mono text-sm transition-colors ${
                i === current
                  ? "border-speak bg-speak/10 text-speak"
                  : "border-hairline text-muted hover:border-faint"
              }`}
            >
              {i + 1}
            </button>
          ))}
        </div>
      </section>

      {/* ---------------- rail ---------------- */}
      <aside className="flex min-h-0 flex-col gap-4 overflow-hidden">
        <div className="panel flex flex-col items-center gap-4 p-6">
          <VoiceOrb phase={phase} />
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                pl.tone === "speak" ? "bg-speak" : pl.tone === "listen" ? "bg-listen" : "bg-faint"
              } ${pl.tone !== "muted" ? "animate-pulse" : ""}`}
            />
            <span className="font-display text-lg font-semibold">{pl.text}</span>
          </div>
          <p className="text-center text-xs text-muted">
            {voice === "live"
              ? "Speak anytime — it pauses, answers, then resumes the exact sentence."
              : "Tap “Go live” to talk to the presentation."}
          </p>
        </div>

        {/* time budget */}
        <div className="panel p-5">
          <p className="eyebrow mb-4">Time budget</p>
          <div className="flex items-baseline justify-between font-mono">
            <span className="text-3xl font-medium tabular-nums">{clock(status?.elapsed_seconds ?? 0)}</span>
            <span className="text-muted">/ {clock(status?.budget_seconds ?? 0)}</span>
          </div>
          <div className="mt-4 flex items-center justify-between text-sm">
            <span className="text-muted">Pacing</span>
            <span className={`font-mono ${overBudget ? "text-over" : "text-listen"}`}>
              {drift >= 0 ? "+" : ""}
              {(drift * 100).toFixed(1)}%
            </span>
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-hairline">
            <div
              className={`h-full rounded-full ${overBudget ? "bg-over" : "bg-listen"}`}
              style={{ width: `${Math.min(100, Math.abs(drift) * 100 + 4)}%` }}
            />
          </div>
          <div className="mt-5 flex items-center justify-between text-sm">
            <span className="text-muted">Track</span>
            <span className="font-mono text-text">{status?.current_track ?? "STANDARD"}</span>
          </div>
        </div>

        {/* speaker notes for the current slide */}
        {slide?.notes && (
          <div className="panel p-5">
            <p className="eyebrow mb-3">Notes · slide {current + 1}</p>
            <p className="text-sm leading-relaxed text-muted line-clamp-[8]">{slide.notes}</p>
          </div>
        )}
      </aside>

      <audio ref={audioRef} autoPlay className="hidden" />
    </div>
  );
}

/* ---- small presentational helpers ---- */
function Icon({ children }: { children: React.ReactNode }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      {children}
    </svg>
  );
}

function TransportBtn({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="flex h-10 w-10 items-center justify-center rounded-lg border border-hairline transition-colors hover:border-faint disabled:opacity-30"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {children}
      </svg>
    </button>
  );
}
