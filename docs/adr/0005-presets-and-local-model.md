# ADR 0005: Three presets; the local model is Gemma 4 12B on Ollama

- **Status:** accepted (presets: operator decision 2026-09-26, OD-003; local runtime and pin:
  OD-232, OD-235, OD-240, OD-242, OD-245, OD-246, 2026-09-30); implemented in V1.3
- **Context source:** SPEC §4.2, §7.5, §12.2, §12.4, §21.2; design plan
  (`docs/history/design-plan-2026-09-27.md`) appendix "Runtimes and models"

## Context

ecf classifies business mail and proposes actions. The operator wants it to work with no cloud
model at all, to use Claude where it helps, and to keep email content to the destinations in the
SPEC privacy statement (§12.4). v1 runs on one Mac (Linux verified in V1.6), so the local model has
to fit a laptop's memory and heat, and its output has to be checked before anything trusts it.

## Decision

- **Three presets per address, nothing in between** (OD-003, §4.2): **A** all-local (classifier
  and actor on the local model); **B** local classifier with a Claude actor; **C** all-Claude.
  Config rejects any other combination. Claude runs only on demand, in `/ecf-review` (V1.4).
- **The local model is Gemma 4 12B** (Google, Apache-2.0), Ollama tag `gemma4:12b`, Q4_K_M GGUF,
  pinned by manifest digest (§7.5). Models from PRC-affiliated and Meta/X-affiliated labs are
  excluded (operator preference, CLAUDE.md).
- **Ollama runs it**, from ecf's own login item with a fixed environment: loopback only, one
  request at a time, cloud off, no debug, flash-attention or q8-cache options (OD-246). On the
  development Mac Ollama is the Homebrew formula, pinned (OD-232).
- **The pin ships in the `ecf_server` wheel** (OD-235) and ecf runs its own copy
  (`ecf/gemma4-12b:<release>`), checked against the pin before every model round; a mismatch stops
  model work (I6).
- **ecf checks the server before each round**: a listener beyond 127.0.0.1 (OD-240), a check that
  can't confirm loopback (OD-242) or request logging on (OD-245) stops model work with a loud
  System Error. The SPEC states the remaining limit: a process on the same computer that can bind
  the port can answer as the model (§12.2).

## Alternatives considered

From the design plan's appendix and the V1.3 build:

- **Native MLX with outlines** instead of Ollama: rejected in the design plan. Ollama's MLX models
  are a roadmap item to evaluate (OD-257), with their own pin and gates.
- **Other local models** (Mistral 7B v0.3, Mistral NeMo, Phi-4, Ministral 3 3B/8B/14B, ModernBERT):
  rejected in the design plan.
- **Haiku via Bedrock or the API in v1:** rejected; v1 has no AWS, and Claude runs only on demand.
- **`brew services` to run Ollama:** rejected (OD-246); its service sets flash attention and the
  q8 cache, which ecf doesn't want, and its environment isn't ecf's to fix.
- **Pinning by tag only, or the pin in the database:** rejected (OD-235); a tag can move upstream,
  and a database value could be changed by anything that writes the database.
- **One field per request with logprobs (single-token confidence):** measured in V1.3 step 10 and
  not adopted (OD-258): 4-7 times slower than one JSON reply.

## Consequences

- Preset A needs about 9 GB free while the model is loaded (8.9 GB measured, §7.5), and runs
  about three times slower on battery (§21.2); model work pauses for heat and follows power
  (OD-029, OD-243, OD-248).
- Every model change (a new pin) is a new digest: the go-live gate starts again for it (ADR 0006).
- An ecf upgrade changes the ecf copy's name; the service copies the pinned model again by itself
  when Ollama still holds it unchanged, and `ecf models install` removes copies for other releases.
- Linux runs the same design through a systemd user unit, unverified until V1.6.
