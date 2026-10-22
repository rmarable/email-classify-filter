# ADR 0036: Google Gemini as a judge provider for generated eval sets

- **Status:** rejected (operator decision 2026-10-22, OD-550: no new platform; the judges are
  Mistral Large 4 and Sol). Was proposed (operator decisions 2026-10-20, OD-547).
- **Context source:** `planning-docs/EVAL-GENERATE-GRADE.md` (draft 20, Judges; Phase R J1-J20)
- **Amends:** ADR 0034 (the closed provider list)

## Context

The operator no longer labels generated sets: two or three non-Claude judges of different
families set the labels, and the operator settles only their disagreements (OD-547). With Claude
generating and classifying, the judges must come from the other families ecf supports. Mistral is
one; OpenAI's Sol is limited by the ChatGPT Plus message allowance and can be at most a
tie-breaker. A second full judge needs a new provider, which ADR 0034 requires to be its own ADR
with the plan-versus-API comparison (decision 5a).

## Decision

- **Google Gemini through its API** joins the closed list for the judge role only:
  `gemini-3.8-flash`, one call per email, a strict JSON schema in `response_format`, from the
  service with a key the service keeps in the macOS Keychain (`ecf models api-key set --provider
  gemini`), sent in the `x-goog-api-key` header, never in a URL.
- **Plan versus API** (decision 5a; Google's pages fetched 2026-10-20): no consumer subscription
  (AI Plus, Pro, Ultra) includes API access, and the Gemini CLI stopped serving subscription
  sign-ins on 2026-06-18, so the API is the only route. Paid tier only: its terms say prompts are
  not used to improve Google's products; the free tier's are, with human review. Prepaid credit
  (US$5 minimum; the key stops at US$0) is the cap, with `--max-cost` as ecf's own guard.
- **What it receives:** the same as any judge under ADR 0034: each generated email as a mail
  client shows it, and the shipped field definitions; never real mail or anything else.
- **The first-use notice** says the paid tier is required and that ecf can't check which tier a
  key belongs to.

## Consequences

- A new destination for synthetic text: SPEC §12.4 gains a paragraph (payload, route, the vendor's
  stated retention and training terms with page and date); README "Your privacy", DISCLAIMER and
  SECURITY.md change with it.
- Google's usage policy bars content facilitating phishing or fraud; labelling a synthetic email
  isn't addressed there, and Gemini, like any judge, may refuse: refusals go to the operator.
- Prices change (US$0.75/$3.75 per 1M tokens to 2026-12-31, then double): the price table in
  `eval_models.json` carries its date and source.
