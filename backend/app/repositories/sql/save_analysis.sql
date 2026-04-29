INSERT INTO analyses(
  id, project_id, scope, chapter_id, chapter_title, status, engine, result_path,
  segment_count, lyric_count, created_at, updated_at
)
VALUES (
  :id, :project_id, :scope, :chapter_id, :chapter_title, 'succeeded', :engine,
  :result_path, :segment_count, :lyric_count, :created_at, :created_at
);
