SELECT id, project_id, scope, chapter_id, chapter_title, status, engine, result_path,
       segment_count, lyric_count, error, created_at, updated_at
FROM analyses
WHERE project_id = :project_id
ORDER BY created_at DESC;
