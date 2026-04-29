SELECT id, project_id, status, output_path, output_url, message, warnings_json, error, created_at, updated_at
FROM renders
WHERE project_id = :project_id AND status = 'succeeded'
ORDER BY updated_at DESC
LIMIT 1;
