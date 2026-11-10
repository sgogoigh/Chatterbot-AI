# Chatterbot-AI docs

| Doc | What it covers |
|---|---|
| [`PLAN.md`](./PLAN.md) | Product blueprint from the source document: goals, the six mechanisms, architecture, success gates. |
| [`IMPLEMENTATION.md`](./IMPLEMENTATION.md) | Backend spec: modules, config, each service, concurrency rules, Docker, recheck log (R1–R15). |
| [`E2E_TESTING.md`](./E2E_TESTING.md) | Test strategy and **current project status** (§0): what's verified, what's open. |
| [`WINDOWS_TESTING.md`](./WINDOWS_TESTING.md) | Running the full pipeline on Windows without Docker. |
| [`ERRORS.md`](./ERRORS.md) | Numbered log of every problem found and how it was fixed. |
| [`DEPLOYMENT.md`](./DEPLOYMENT.md) | Production: Vercel (frontend) + Fly.io (backend) + LiveKit Cloud. |

Setup and running locally: the root [`README.md`](../README.md). Component
READMEs: [`backend/README.md`](../backend/README.md), [`frontend/README.md`](../frontend/README.md).
