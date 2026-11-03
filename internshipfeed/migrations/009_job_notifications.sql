-- Create the original notification ledger with candidate/job/channel uniqueness.
CREATE TABLE job_notifications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_profile_id BIGINT NOT NULL REFERENCES candidate_profiles(id) ON DELETE CASCADE,
    canonical_job_id BIGINT NOT NULL REFERENCES canonical_jobs(id),
    channel TEXT NOT NULL CHECK (channel = 'sms'),
    provider_message_sid TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (candidate_profile_id, canonical_job_id, channel)
);
