"""transport package — pluggable audio I/O for the agent worker (WINDOWS_TESTING.md §3.2).

A transport provides both halves of the agent's audio contract:
  * INPUT  — async-iterable yielding float32 mono frames at 16 kHz (for VAD/STT)
  * OUTPUT — a sink with ``async play(samples, sr)`` and ``async stop()`` (barge-in)

Implementations: LocalAudioTransport (mic/speakers), FileAudioTransport (WAV,
headless), LiveKitTransport (WebRTC). The worker is agnostic to which is used.
"""
