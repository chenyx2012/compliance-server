-- compliance_gateway 增量 DDL（相对早期网关版本）
-- 在 MySQL 上执行：mysql -u root -p compliance_gateway < scripts/migrate_schema_v2.sql
-- 已存在的列/表会报错可忽略（或手工注释对应语句）

USE compliance_gateway;

-- platform_task
ALTER TABLE platform_task
  ADD COLUMN target_repo_name VARCHAR(64) NULL COMMENT '片段扫描目标知识库名' AFTER s3_conflict_count;
CREATE INDEX idx_target_repo_name ON platform_task (target_repo_name);

-- file_ingest_result
ALTER TABLE file_ingest_result
  ADD COLUMN source_file_path VARCHAR(1024) NULL AFTER s3_upload_status,
  ADD COLUMN task_id VARCHAR(64) NULL AFTER source_file_path;
ALTER TABLE file_ingest_result MODIFY COLUMN s3_upload_status VARCHAR(512) NULL;
CREATE INDEX idx_file_ingest_task_id ON file_ingest_result (task_id);

-- code_file
CREATE TABLE IF NOT EXISTS code_file (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  ingest_id BIGINT NULL,
  task_id VARCHAR(64) NULL,
  file_path VARCHAR(512) NOT NULL,
  content MEDIUMTEXT NULL,
  file_size INT NOT NULL DEFAULT 0,
  extension VARCHAR(32) NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_code_file_ingest_id (ingest_id),
  INDEX idx_code_file_task_id (task_id),
  CONSTRAINT fk_code_file_ingest FOREIGN KEY (ingest_id) REFERENCES file_ingest_result(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- open_source_code_file
CREATE TABLE IF NOT EXISTS open_source_code_file (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  repo_name VARCHAR(255) NOT NULL,
  file_path VARCHAR(512) NOT NULL,
  content LONGTEXT NULL,
  file_size INT NOT NULL DEFAULT 0,
  extension VARCHAR(32) NULL,
  s3_path VARCHAR(1024) NULL,
  remote_url VARCHAR(1024) NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_os_repo_name (repo_name),
  INDEX idx_os_file_path (file_path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- scan_result_code_clone
CREATE TABLE IF NOT EXISTS scan_result_code_clone (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  task_id VARCHAR(64) NOT NULL,
  path1 VARCHAR(1024) NULL,
  start1 INT NULL,
  end1 INT NULL,
  path2 VARCHAR(1024) NULL,
  start2 INT NULL,
  end2 INT NULL,
  similarity FLOAT NOT NULL,
  ai_judgment VARCHAR(50) NULL,
  ai_reasoning TEXT NULL,
  created_at DATETIME NOT NULL,
  INDEX idx_clone_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ai_false_positive_cases
CREATE TABLE IF NOT EXISTS ai_false_positive_cases (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  code_snippet TEXT NOT NULL,
  code_snippet_hash VARCHAR(64) NOT NULL,
  code_type VARCHAR(50) NULL,
  judgment_reason TEXT NULL,
  source VARCHAR(100) NULL,
  task_id VARCHAR(100) NULL,
  case_index INT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uk_code_snippet_hash (code_snippet_hash),
  INDEX idx_fp_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- open_source_code_repo_mapping（44 条种子由应用 init_db 写入；此处仅建表）
CREATE TABLE IF NOT EXISTS open_source_code_repo_mapping (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  path_prefix VARCHAR(255) NOT NULL,
  org_name VARCHAR(128) NULL,
  repo_name VARCHAR(128) NULL,
  remote_base_url VARCHAR(512) NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uk_path_prefix (path_prefix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
