-- Corpus (SPEC §16.7; OD-466, OD-467): bytes a one-off corpus fetch downloaded per mailbox per
-- hour (UTC), keyed by the folded email (internal.fold) because the mailbox may not be watched.
-- A fetch with --address records in `downloads` instead. Pruned like `downloads`.
CREATE TABLE corpus_downloads (
    email_norm TEXT NOT NULL,
    hour TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK (bytes >= 0),
    PRIMARY KEY (email_norm, hour)
) STRICT;
