INSERT INTO project_assets(id, project_id, asset_type, path, filename, size_bytes, created_at, metadata_json)
VALUES (:id, :project_id, :asset_type, :path, :filename, :size_bytes, :created_at, :metadata_json)
ON CONFLICT(project_id, asset_type, path) DO UPDATE SET
  size_bytes = excluded.size_bytes,
  -- Startup re-imports re-register existing files without metadata. Overwriting unconditionally
  -- wiped metadata written by the upload paths on every restart.
  metadata_json = CASE
    WHEN :overwrite_metadata = 1 THEN excluded.metadata_json
    ELSE project_assets.metadata_json
  END;
