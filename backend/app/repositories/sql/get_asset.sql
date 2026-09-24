SELECT metadata_json
FROM project_assets
WHERE project_id = :project_id AND asset_type = :asset_type AND path = :path
