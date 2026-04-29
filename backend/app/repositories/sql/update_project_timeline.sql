UPDATE projects
SET status = 'timeline_saved', updated_at = :updated_at
WHERE id = :project_id;
