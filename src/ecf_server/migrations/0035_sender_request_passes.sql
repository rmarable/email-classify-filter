-- Sender history from code, gift-card and crypto requests (pre-gate review G1 refinement,
-- operator decision 2026-10-10; SPEC §8.5): a DMARC-pass message whose only fraud reason was
-- such a request from a sender with no history still counts toward the history, and is also
-- counted here. A sender is known by history only when at least one of its passes was other
-- mail, so a domain that only ever sends such requests never becomes known.
ALTER TABLE senders ADD COLUMN request_pass_count INTEGER NOT NULL DEFAULT 0;
