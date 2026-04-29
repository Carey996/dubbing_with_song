UPDATE projects
SET status = 'failed', updated_at = :updated_at
WHERE id = :project_id;
