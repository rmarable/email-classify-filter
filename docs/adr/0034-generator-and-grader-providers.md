# ADR 0034: Generator and grader providers for generated eval sets

- **Status:** accepted (operator decisions 2026-10-19; build approved 2026-10-20, OD-534 to
  OD-539)
- **Context source:** `planning-docs/EVAL-GENERATE-GRADE.md` (draft 8), Phase R P1-P31
  (2026-10-19)

## Context

Every figure ecf has comes from Claude reading Claude's mail: the synthetic set was written by
Claude, and the classifier and actor are Claude. The operator wants a second set that no Claude
model wrote, and a way for users to rate ecf with models other than Claude: one model writes test
mail (the generator), ecf handles it as usual, and a model from a third family labels it blind
(the grader). That sends synthetic text to providers SPEC's one-destination rule doesn't list.

## Decision

- **A closed list of providers.** Generators and graders run only through these: OpenAI, through
  the Codex CLI on the user's own ChatGPT sign-in; Mistral AI, through its API with a key the
  service keeps in the macOS Keychain; Anthropic (already a destination), through `claude -p` on
  ecf's own Claude sign-in, for DEV runs. A new provider needs its own ADR, which compares the
  provider's plan with its API (cost, tool access, data sent, credential storage, data terms,
  limits; decision 5a).
- **What each receives.** The generator: ecf's shipped published lists (FBI IC3 crime types and
  definitions, MITRE delivery methods, Gmail's inbox categories), the fictitious recipient
  configuration, the safety rules, the fixed purpose statement and the output schema. The grader:
  each generated email and the shipped field definitions. Never real mail, facts, signals,
  results, labels, the service's data or text from a user's mix file. The commands refuse a set
  that isn't generated.
- **Tool-less only.** No provider is used until it is shown to answer with no tool access and no
  MCP servers: a CLI must pass the canary (a decoy file in its work folder; any answer holding its
  contents, or any tool event, fails), and runs under an allow-listed environment, refusing to run
  while an API key variable is set.
- **Separate sign-ins.** Each CLI runs on a sign-in kept for ecf (Codex in its own `CODEX_HOME`,
  stored in the Keychain; Claude in ecf's `CLAUDE_CONFIG_DIR`), never the user's everyday one.
- **Never the gate's evidence.** A generated set never counts for the go-live gate and never
  re-points it.

## Alternatives considered

- **Mistral Vibe as the grader:** its core always offers file and shell tools to the model, which
  can only be refused call by call; no model flag; no schema mode. Rejected for the API.
- **Each provider's API for every role:** costlier for OpenAI, which the operator already pays
  for through a plan; chosen per provider instead (decision 5a).
- **Claude writing the second set:** defeats its purpose (decision 1).

## Consequences

- SPEC §12.4 gains a paragraph per provider, and README, DISCLAIMER and SECURITY.md change with it
  (build step 6).
- Refusals are recorded and never retried, reworded or replaced, so a group can come up short.
- Usage-policy and account risk on consumer plans stays with the user, who is told what goes where
  before each run.
