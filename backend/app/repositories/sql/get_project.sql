SELECT id, title, created_at, updated_at, status, text_length, source_path, current_analysis_id, current_render_id
FROM projects
WHERE id = :project_id;
