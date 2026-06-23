"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

/* Transient error popups. A toast appears only when an action fails (e.g. a
   404 or a network error from a button click), is dismissable via its × button,
   and clears itself after at most 4 seconds. Background pollers stay silent —
   only user-initiated failures surface here. */

type Toast = { id: number; message: string };

const ToastCtx = createContext<(message: string) => void>(() => {});

/** Raise a red error toast. Call from action handlers, not from pollers. */
export function useToast() {
  return useContext(ToastCtx);
}

let nextId = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const notify = useCallback((message: string) => {
    const id = nextId++;
    setToasts((t) => [...t, { id, message }]);
  }, []);

  return (
    <ToastCtx.Provider value={notify}>
      {children}
      <div className="pointer-events-none fixed top-4 right-4 z-50 flex w-[min(92vw,22rem)] flex-col gap-2">
        {toasts.map((t) => (
          <ToastItem key={t.id} message={t.message} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

function ToastItem({ message, onClose }: { message: string; onClose: () => void }) {
  // Auto-dismiss after 4s; the × closes it sooner.
  useEffect(() => {
    const id = setTimeout(onClose, 4000);
    return () => clearTimeout(id);
  }, [onClose]);

  return (
    <div
      role="alert"
      className="rise pointer-events-auto flex items-start gap-3 rounded-xl border border-over/50 bg-over/15 px-4 py-3 text-sm text-text shadow-lg backdrop-blur"
    >
      <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-over" aria-hidden />
      <span className="flex-1 leading-snug">{message}</span>
      <button
        onClick={onClose}
        aria-label="Dismiss"
        className="-mr-1 -mt-0.5 shrink-0 rounded p-1 text-muted transition-colors hover:text-text"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>
  );
}
