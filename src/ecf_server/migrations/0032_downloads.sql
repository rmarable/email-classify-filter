-- V1.6 step 3b (OD-440): bytes fetch downloaded per address per hour (UTC), for Gmail's daily
-- download budget. Rows older than two days are pruned as new ones are written.
CREATE TABLE downloads (
    address_id TEXT NOT NULL REFERENCES addresses (address_id) ON DELETE CASCADE,
    hour TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK (bytes >= 0),
    PRIMARY KEY (address_id, hour)
) STRICT;
