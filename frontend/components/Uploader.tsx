"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useToast } from "./Toast";

/* Step 1 — bring in a deck. A single, obvious drop target ("stage door"). */
export default function Uploader({
  onUploaded,
}: {
  onUploaded: (jobId: string, slideCount: number, fileName: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const notify = useToast();

  async function handleFile(file: File) {
    if (!file.name.toLowerCase().endsWith(".pptx")) {
      notify("That isn't a .pptx file. Export your deck as PowerPoint and try again.");
      return;
    }
    setBusy(true);
    try {
      const res = await api.upload(file);
      onUploaded(res.job_id, res.slide_count, file.name);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rise mx-auto w-full max-w-2xl">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f) handleFile(f);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
        aria-label="Upload a .pptx file"
        className={`flex cursor-pointer flex-col items-center justify-center gap-4 rounded-2xl border-2 border-dashed px-8 py-10 text-center transition-colors ${
          dragging
            ? "border-speak bg-speak/5"
            : "border-hairline bg-panel hover:border-faint"
        }`}
      >
        <div
          className={`flex h-14 w-14 items-center justify-center rounded-full border transition-colors ${
            dragging ? "border-speak text-speak" : "border-hairline text-muted"
          }`}
          aria-hidden
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 16V4m0 0L7 9m5-5 5 5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 17v2a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2" strokeLinecap="round" />
          </svg>
        </div>
        <div>
          <p className="font-medium">
            {busy ? "Reading your deck…" : "Drop your .pptx here"}
          </p>
          <p className="mt-1 text-sm text-muted">or click to choose a file · up to 50 slides</p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pptx"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        />
      </div>
    </div>
  );
}
