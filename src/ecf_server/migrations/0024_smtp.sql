-- 0024 (SPEC §8.4; OD-324; V1.5 step 1a): where each address sends mail.
-- - addresses.smtp_host, smtp_port: the submission server (465 TLS, or 587 with STARTTLS). Set at
--   `address add` (default: the IMAP host with `imap.` replaced by `smtp.`, port 465); changing
--   it needs step-up and a Security Notice, since the app password goes there.
-- - probe.smtp: the last SMTP check (JSON: ok, size limit, 8BITMIME, or the error); never sends.

ALTER TABLE addresses ADD COLUMN smtp_host TEXT;

ALTER TABLE addresses ADD COLUMN smtp_port INTEGER
    CHECK (smtp_port IS NULL OR smtp_port IN (465, 587));

ALTER TABLE probe ADD COLUMN smtp TEXT;

UPDATE addresses SET
    smtp_host = (SELECT 'smtp.' || substr(p.host, 6) FROM probe p
                 WHERE p.address_id = addresses.address_id AND lower(p.host) LIKE 'imap.%'),
    smtp_port = 465
WHERE EXISTS (SELECT 1 FROM probe p
              WHERE p.address_id = addresses.address_id AND lower(p.host) LIKE 'imap.%');
