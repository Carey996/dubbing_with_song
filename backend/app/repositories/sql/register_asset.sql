INSERT INTO project_assets(id, project_id, asset_type, path, filename, size_bytes, created_at, metadata_json)
VALUES (:id, :project_id, :asset_type, :path, :filename, :size_bytes, :created_at, :metadata_json)
ON CONFLICT(project_id, asset_type, path) DO UPDATE SET
  size_bytes = excluded.size_bytes,
  metadata_json = excluded.metadata_json;
