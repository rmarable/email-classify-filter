# Why ecf doesn't use Jev: a plain-language summary

Written 2026-10-15. The technical version, with every figure and its source, is
`planning-docs/JEV-EXPERIMENTS-SUMMARY.md`.

## The short version

We tested a second AI model, called Jev, to see whether it could take over one of Claude's jobs in
ecf: reading each incoming email and deciding what kind of email it is. Jev is fast, cheap and
careful about scams, but it gets too many ordinary emails wrong. The only way to make it as good as
Claude was to hand most of the hard emails back to Claude, and at that point there's little reason
to use Jev at all. So ecf version 3.0.0 uses Claude alone.

## What ecf does, and where Jev would fit

ecf watches a business mailbox. For each new email it answers a set of questions:

- Is this a scam or phishing attempt?
- Is someone asking us to change bank details or send money?
- Does it need a reply, or someone to do something?
- How urgent is it?
- What kind of email is it (an invoice, a newsletter, a customer question…)?

Those answers decide what happens next: leave it in the inbox, file it away, or alert a person.
Today Claude answers the questions. Jev, made by a company called TypeSafe, is a model built
specifically for answering questions like these, so we tested whether it could do that part
instead.

## How we tested it

We never sent Jev any real email. We wrote 304 made-up test emails covering everything ecf has to
handle: genuine invoices, newsletters, customer requests, and many kinds of scams, including emails
that try to trick the AI. For each one we know the right answers. We ran Jev and Claude over the
same emails, several times each, and compared how often each got the answers right.

We also tried to help Jev: rewording the questions, changing their order, giving it extra hints,
and trying a newer version of Jev.

## What Jev is good at

- **Spotting scams.** Jev caught more phishing emails than Claude did: roughly 96 out of 100,
  against Claude's 90. It also raised a few more false alarms.
- **Safety.** Jev never made a dangerous mistake in our tests. It never hid a scam, never missed
  one of the fraud warnings ecf checks for, and was never fooled by emails that tried to give it
  instructions.
- **Speed.** Jev answers in about a tenth of a second per email. Claude handles email in batches
  that take minutes.
- **Cost.** Running Jev over all 304 test emails cost about 3 cents. Claude costs about $5 for
  the same work.

## What Jev is bad at

- **Getting ordinary emails right.** Overall, Jev answered everything correctly for about 69 of
  every 100 emails. Claude managed about 84. That gap is too big to be luck.
- **Knowing whether an email needs action.** Jev often said an email needed something done when it
  didn't, so ecf would bother people about mail that could have been filed away.
- **Judging urgency and the type of email.** It made noticeably more mistakes here than Claude.
- **Rating fraud risk.** It sometimes rated suspicious emails as lower risk than it should have.
  (ecf's own fixed safety checks still caught the dangerous ones.)
- **Consistency.** Its answers changed depending on how we worded the question or what order we
  listed the options in. A good classifier shouldn't care about that.
- **The newer version was worse**, not better.

## Why not use both?

The obvious idea: let Jev handle every email first, and pass only the emails it's unsure about to
Claude. We worked out what that would take. To match Claude's accuracy, **about 60% of all email
would still have to go to Claude**, and often 65 to 75%. That would leave:

- **Little saved time.** Most emails would still wait for Claude.
- **Little saved money.** Claude would still be paid for most of the work.
- **No gain in accuracy.** At best, the mix only matched Claude alone.
- **More privacy exposure.** Every email would go to TypeSafe, and most would also go to Anthropic
  (Claude's maker). That's two outside companies seeing your email instead of one. ecf's rule is to
  send email to as few places as possible.
- **More complexity.** Two systems to run, check and keep up to date, and a borderline email might
  go one way today and the other way tomorrow.

We also considered letting Claude do the job and having Jev act only as an extra scam detector on
the side. That caught the extra scams, but it also raised about 20 extra false alarms per test
round and still sent every email to both companies.

## The decision

On 2026-10-15 the operator decided (recorded as OD-516):

- ecf 3.0.0 uses Claude alone to read and sort email.
- Jev is kept for testing only, and no real email goes to it.
- The Jev-based setup that was built stays in the code, switched off.
- **Rule for the future:** Jev will be used only if a later version can do the whole job on its
  own at least about as well as Claude. Jev and Claude won't be mixed.

## What would change our minds

A future version of Jev that, on the same test, gets close to Claude's accuracy by itself while
staying as safe. We've set aside 30 new test emails that neither model has seen, ready for that
re-test.

## Caveats

- All testing used made-up emails. Real mail could make either model look better or worse.
- Jev is changed by its maker from time to time, so these results describe the version we tested
  (1.13.0) in October 2026.
