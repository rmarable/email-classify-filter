# M6 planning (parked 2026-10-22)

> **Work on this document in plan mode.** When M6 resumes, start the session in plan mode
> (Shift+Tab, or /plan), read this file, and settle the open choices before anything is
> written to the repo.


## Context
On 2026-10-22 the operator decided to park M6 again. Before parking, two read-only agents
researched Google's rules for Testing and publishing, and the console steps. Nothing has been
written to the repo. The only thing left to do is to save the findings, so a later restart
doesn't redo the research.

## Steps (local, gitignored files only; no repo changes, no commit)
1. Write `state-archive/m6/research-2026-10-22.md` (gitignored). It records:
   - the findings below, with their source URLs;
   - the one decision taken: the guide tells people to paste the client secret straight into
     `ecf gmail client set` and, if the screen is closed, to add a new secret (no JSON file).
   - the open choices: how to publish (own domain, github.io test, or weekly sign-in, which
     touches OD-528), whether billing or a card is needed, and which click path to give.
2. `CLAUDE-STATE.md`: re-read it first. Change the M6 lines to "PARKED again 2026-10-22
   (operator)" plus a one-line pointer to the research file. Add one line to Recent progress.

## Findings to save (Google pages, fetched 2026-10-22)
- **Testing** (support.google.com/cloud/answer/15549945):
  - Up to 100 test users.
  - Gmail scopes expire 7 days after consent.
  - Add yourself as a test user: Audience, then Test users, then Add users
    (developers.google.com/workspace/guides/configure-oauth-consent).
- **Publish greyed out (cause unverified):**
  - Homepage, privacy policy and terms links are "required for all external production apps".
  - They must sit on a verified domain you own (Search Console), not a third-party host
    (support.google.com/cloud/answer/15549049, 13807376, 13806988).
  - GitHub isn't mentioned: a github.com repo URL would likely fail; github.io is unverified.
  - Risk to OD-528: every operator might need their own domain.
- **Client secret** (support.google.com/cloud/answer/15549257):
  - Shown only once, at creation (since June 2025). A lost one is replaced by rotating it
    (at most 2 secrets at a time).
  - A client unused for 6 months is deleted: an email comes 30 days before, and a deleted
    client can be restored within 30 days.
- **Project** (docs.cloud.google.com/resource-manager/docs/creating-managing-projects):
  - The name is 4 to 30 characters, so the guide's `ecf` is invalid.
  - The ID is 6 to 30 characters and permanent.
  - Google's guide path is IAM & Admin, then Create a Project.
- **Billing:**
  - The Gmail API is free at standard use (developers.google.com/workspace/gmail/api/reference/quota).
  - Charges above quota are planned for later in 2026, with 90 days' notice.
  - Whether a card or free trial is needed is unverified.
- **Desktop OAuth** (developers.google.com/identity/protocols/oauth2/native-app):
  - Loopback and PKCE S256 are current. ecf must send `code_challenge_method` explicitly,
    because it defaults to plain.
  - OOB is not supported.
- **Docs fixes for later:**
  - Guide, `gmail-setup.md` on branch `m6-gmail-api`:
    - line 42, project name;
    - lines 59-63, Publish;
    - lines 70-71, the secret is shown once;
    - line 27, the billing claim is too strong;
    - add a test-user step.
  - Roadmap line 345: it cites the quickstart for Publish, but the quickstart uses Internal.
  - Note the 6-month client deletion.

## Verification
The research file exists. The state file's M6 entry says parked, with the pointer.
`git status` shows no new tracked changes.
