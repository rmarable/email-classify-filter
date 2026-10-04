-- V1.5 step 9c (OD-360): rows brought in by `ecf import` carry the source install's ID.
ALTER TABLE items ADD COLUMN origin TEXT;
ALTER TABLE audit ADD COLUMN origin TEXT;
