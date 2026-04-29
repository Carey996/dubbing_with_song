INSERT INTO projects(id, title, created_at, updated_at, status, text_length, source_path)
VALUES (:id, :title, :created_at, :updated_at, :status, :text_length, :source_path)
ON CONFLICT(id) DO NOTHING;
