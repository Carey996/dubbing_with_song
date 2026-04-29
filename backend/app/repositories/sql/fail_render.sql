UPDATE renders
SET status = 'failed', error = :error, updated_at = :updated_at
WHERE id = :id AND project_id = :project_id;
