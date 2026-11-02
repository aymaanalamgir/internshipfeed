-- Preserve historical notification audit rows while moving runtime delivery to email.
ALTER TABLE job_notifications RENAME COLUMN provider_message_sid TO provider_message_id;
ALTER TABLE job_notifications DROP CONSTRAINT job_notifications_channel_check;
ALTER TABLE job_notifications ADD CONSTRAINT job_notifications_channel_check CHECK (channel IN ('sms', 'email'));
