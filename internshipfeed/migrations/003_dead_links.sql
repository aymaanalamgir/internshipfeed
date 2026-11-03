-- Track dead application links and their last verification time.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check CHECK (status IN ('open', 'closed', 'unknown', 'dead'));
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS link_checked_at TIMESTAMPTZ;
