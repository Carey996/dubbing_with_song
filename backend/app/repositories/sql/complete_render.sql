UPDATE renders
SET status = 'succeeded',
    output_path = :output_path,
    output_url = :output_url,
    message = :message,
    warnings_json = :warnings_json,
    updated_at = :updated_at
WHERE id = :id AND project_id = :project_id;
