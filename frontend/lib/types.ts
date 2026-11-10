// Shared types mirroring the FastAPI backend schemas (backend/models.py).
// Kept in sync by hand; these are the contracts the UI relies on.

export type Persona = "GENERAL" | "TEACHER" | "MEETING" | "TEDX";
export type Track = "STANDARD" | "SUMMARY" | "TURBO";
export type Intent = "NEXT" | "PREV" | "GOTO" | "QUESTION" | "STOP" | "IGNORE";

export type SessionPhase =
  | "IDLE" | "LOADING" | "READY" | "SPEAKING" | "LISTENING"
  | "PROCESSING" | "ANSWERING" | "PAUSED" | "TRANSITIONING" | "ENDED";

export interface UploadResponse {
  job_id: string;
  slide_count: number;
}

export interface BuildResponse {
  job_id: string;
  build_id: string;
  state: string;
}

export interface BuildStatus {
  job_id: string;
  state: "queued" | "running" | "done" | "error";
  slides_done: number;
  total: number;
  tracks_built: Track[];
  estimated_seconds: Partial<Record<Track, number>>;
  error: string | null;
}

export interface SlideContent {
  index: number;
  title: string | null;
  body_text: string;
  notes: string;
  ocr_text: string;
  image_paths: string[];
  render_path: string | null;
}

export interface StartSessionResponse {
  session_id: string;
  room_name: string;
  livekit_url: string;
  token: string;
}

export interface StatusResponse {
  session_id: string;
  phase: SessionPhase;
  current_slide: number;
  current_track: Track;
  elapsed_seconds: number;
  budget_seconds: number;
  drift_pct: number;
}
