# Voice clip recording script — S14b (mic → answer) test set

These clips test the **microphone → answer** direction I can't synthesize: real
human speech through VAD → Faster-Whisper STT → intent → RAG → Groq → reply.
They're recorded against `test_assets/terragrid_q3.pptx` (5 slides), so every
question has a definite, gradeable answer.

## Recording format
- **One utterance per file**, WAV if possible (mono). 16 kHz is ideal; anything
  common (44.1 kHz, m4a, mp3) is fine — I resample/decode.
- **Quiet room**, no background music/TV. Normal speaking volume, ~30 cm from the
  mic, don't shout (avoid clipping).
- **Silence padding:** ~0.7 s of silence **before** you start and ~1.0 s **after**
  you finish each clip. (The VAD uses trailing silence to know you're done.)
- **Tone:** natural and conversational — like talking to a colleague — not robotic.
  Moderate pace. English, single speaker (you), same voice across all clips.
- Put the files in **`test_assets/clips/`** using the exact filenames below.

## The deck (so you know what's true)
1. Title — TerraGrid Q3 2026 Review
2. Revenue — **$12.4M**, up **18%** from Q2; subscription = **$9M**
3. Customers — **1,200** new; churn **3%**; enterprise = **40%** of base
4. Product — launched **Atlas dashboard** + **mobile app**; **70%** adopted Atlas in month 1
5. Outlook — target **$15M** next quarter; expanding to **Europe, Japan, Brazil**

## Clips to record

### Navigation & control (say these as directives — calm, clear)
| File | Say exactly | Tone / notes |
|------|-------------|--------------|
| `next.wav` | "Next slide." | brief, directive |
| `previous.wav` | "Go back to the previous slide." | directive |
| `goto.wav` | "Go to slide four." | directive; say "four" clearly |
| `stop.wav` | "Stop for a moment." | calm |
| `move_on.wav` | "Let's move on." | casual — tests that this maps to NEXT |

### Acknowledgement (should NOT trigger an answer)
| File | Say exactly | Tone / notes |
|------|-------------|--------------|
| `ignore.wav` | "Okay, got it." | casual, low-energy backchannel |

### Questions — answerable from the deck (curious tone)
| File | Say exactly | Expected answer keys to |
|------|-------------|--------------|
| `q_revenue.wav` | "What was the revenue this quarter?" | $12.4M / 18% (slide 2) |
| `q_customers.wav` | "How many new customers did we add?" | 1,200 (slide 3) |
| `q_regions.wav` | "Which regions are we expanding into next?" | Europe, Japan, Brazil (slide 5) |
| `q_product.wav` | "What did we launch this quarter?" | Atlas dashboard + mobile app (slide 4) |
| `q_long.wav` | "Can you explain how much revenue grew compared to last quarter, and what drove it?" | longer (>2 s) — tests endpointing; ~18% growth, subscriptions |

### Out-of-deck question (should decline, not invent)
| File | Say exactly | Expected behavior |
|------|-------------|--------------|
| `q_outofdeck.wav` | "What is the company's hiring plan for next year?" | "not in the slides" — must NOT hallucinate |

## Optional (nice-to-have, not required)
- `q_revenue_fast.wav` — same as `q_revenue` but spoken quickly.
- `q_revenue_far.wav` — same, recorded a bit farther from the mic.
  (These let me gauge STT robustness on real audio.)

## What I'll do with them
A clip-runner harness feeds each file through the **real** pipeline and prints:
the STT transcript, the classified intent (and slide for GOTO), the retrieved
source slides, and the grounded answer — graded against the table above. Then,
optionally, I publish a clip into the live LiveKit room as your "mic" to confirm
the full WebRTC round-trip (the agent pausing, answering, and resuming).
