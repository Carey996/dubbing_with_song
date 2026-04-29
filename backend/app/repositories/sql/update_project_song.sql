UPDATE projects
SET status = 'song_uploaded', updated_at = :updated_at
WHERE id = :project_id;
