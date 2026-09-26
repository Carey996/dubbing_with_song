DELETE FROM project_assets
WHERE project_id = :project_id AND path = :path;
