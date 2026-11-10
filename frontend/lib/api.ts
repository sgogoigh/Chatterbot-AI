// Typed client for the Chatterbot-AI FastAPI backend.
// Base URL is configurable so the UI can point at a local or remote backend.

import type {
  BuildResponse,
  BuildStatus,
  Intent,
  Persona,
  SlideContent,
  StartSessionResponse,
  StatusResponse,
  UploadResponse,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

/** Throw a readable error from a failed response (surfaces backend `detail`). */
async function check(res: Response): Promise<Response> {
  if (res.ok) return res;
  let detail = res.statusText;
  try {
    const body = await res.json();
    detail = body?.detail ?? detail;
  } catch {
    /* non-JSON error body — keep statusText */
  }
  throw new Error(`${res.status}: ${detail}`);
}

async function json<T>(res: Response): Promise<T> {
  return (await check(res)).json() as Promise<T>;
}

export const api = {
  /** Liveness check used to show backend connectivity. */
  async health(): Promise<boolean> {
    try {
      const r = await fetch(`${API_BASE}/healthz`, { cache: "no-store" });
      return r.ok;
    } catch {
      return false;
    }
  },

  /** Upload a .pptx; returns the new job id and slide count. */
  async upload(file: File): Promise<UploadResponse> {
    const form = new FormData();
    form.append("file", file);
    return json(await fetch(`${API_BASE}/api/presentations`, { method: "POST", body: form }));
  },

  /** Kick off the (async) build: pacing + 3-track scripts + RAG index. */
  async build(jobId: string, targetMinutes: number, persona: Persona): Promise<BuildResponse> {
    return json(
      await fetch(`${API_BASE}/api/presentations/${jobId}/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_minutes: targetMinutes, persona }),
      }),
    );
  },

  /** Poll build progress. */
  async buildStatus(jobId: string): Promise<BuildStatus> {
    return json(
      await fetch(`${API_BASE}/api/presentations/${jobId}/build/status`, { cache: "no-store" }),
    );
  },

  /** Slide metadata for the viewer. */
  async slides(jobId: string): Promise<SlideContent[]> {
    return json(await fetch(`${API_BASE}/api/presentations/${jobId}/slides`, { cache: "no-store" }));
  },

  /** URL for a slide's rendered image (used directly in <img>). */
  slideImage(jobId: string, index: number): string {
    return `${API_BASE}/api/presentations/${jobId}/slides/${index}/image`;
  },

  /** Create a live session; returns LiveKit join details + token. */
  async startSession(jobId: string): Promise<StartSessionResponse> {
    return json(
      await fetch(`${API_BASE}/api/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId }),
      }),
    );
  },

  /** Manual navigation — routes through the same handler as voice intents. */
  async navigate(sessionId: string, intent: Extract<Intent, "NEXT" | "PREV" | "GOTO">, slideIndex?: number): Promise<StatusResponse> {
    return json(
      await fetch(`${API_BASE}/api/sessions/${sessionId}/navigate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, intent, slide_index: slideIndex ?? null }),
      }),
    );
  },

  /** Pause / resume / stop the session. */
  async control(sessionId: string, action: "pause" | "resume" | "stop"): Promise<StatusResponse> {
    return json(
      await fetch(`${API_BASE}/api/sessions/${sessionId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      }),
    );
  },

  /** Current session phase / slide / drift, for the status rail. */
  async status(sessionId: string): Promise<StatusResponse> {
    return json(await fetch(`${API_BASE}/api/sessions/${sessionId}/status`, { cache: "no-store" }));
  },

  /** Best-effort stop fired as the tab closes (no await, must survive unload).
   *  `keepalive` lets the request outlive the document; same cross-origin JSON
   *  path as control(), so existing CORS applies. Errors are ignored — the
   *  backend evicts any leftover session on the next start anyway. */
  stopBeacon(sessionId: string): void {
    try {
      void fetch(`${API_BASE}/api/sessions/${sessionId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "stop" }),
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* unload context — nothing more we can do */
    }
  },
};
