UPDATE projects
SET status = 'rendered', updated_at = :updated_at, current_render_id = :render_id
WHERE id = :project_id;
