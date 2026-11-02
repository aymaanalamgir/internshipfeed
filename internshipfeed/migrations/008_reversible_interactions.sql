-- Preserve every historical action; inverse actions change the derived state.
ALTER TABLE job_interactions DROP CONSTRAINT job_interactions_interaction_type_check;
ALTER TABLE job_interactions ADD CONSTRAINT job_interactions_interaction_type_check
    CHECK (interaction_type IN ('view', 'click', 'save', 'unsave', 'dismiss', 'applied', 'unapplied'));
