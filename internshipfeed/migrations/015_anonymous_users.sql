-- Give every existing profile a private anonymous owner without changing its history or timestamps.
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE candidate_profiles ADD COLUMN user_id UUID NOT NULL DEFAULT gen_random_uuid();
INSERT INTO users (id) SELECT user_id FROM candidate_profiles;
ALTER TABLE candidate_profiles ALTER COLUMN user_id DROP DEFAULT;
ALTER TABLE candidate_profiles ADD CONSTRAINT candidate_profiles_user_fk FOREIGN KEY (user_id) REFERENCES users(id);
ALTER TABLE candidate_profiles ADD CONSTRAINT candidate_profiles_user_key UNIQUE (user_id);
