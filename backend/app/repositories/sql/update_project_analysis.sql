UPDATE projects
SET status = 'analyzed', updated_at = :updated_at, current_analysis_id = :analysis_id
WHERE id = :project_id;
