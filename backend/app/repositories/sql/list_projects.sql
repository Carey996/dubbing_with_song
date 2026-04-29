SELECT
  p.*,
  EXISTS(SELECT 1 FROM analyses a WHERE a.project_id = p.id AND a.status = 'succeeded') AS has_analysis,
  EXISTS(SELECT 1 FROM project_assets pa WHERE pa.project_id = p.id AND pa.asset_type = 'song_mp3') AS has_song,
  EXISTS(SELECT 1 FROM project_assets pa WHERE pa.project_id = p.id AND pa.asset_type = 'timeline_json') AS has_timeline,
  EXISTS(SELECT 1 FROM renders r WHERE r.project_id = p.id AND r.status = 'succeeded') AS has_render
FROM projects p
ORDER BY p.updated_at DESC;
