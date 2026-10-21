-- PR / 版本扫描场景增量字段
-- mysql -u root -p compliance_gateway < scripts/migrate_schema_v3_pr_version.sql

USE compliance_gateway;

ALTER TABLE platform_task
  ADD COLUMN scan_mode VARCHAR(16) NOT NULL DEFAULT 'version' COMMENT 'pr|version' AFTER target_repo_name,
  ADD COLUMN business_key VARCHAR(255) NULL COMMENT '业务幂等键' AFTER scan_mode,
  ADD COLUMN repo_name VARCHAR(255) NULL AFTER business_key,
  ADD COLUMN pr_number VARCHAR(64) NULL AFTER repo_name,
  ADD COLUMN commit_sha VARCHAR(64) NULL AFTER pr_number,
  ADD COLUMN version_tag VARCHAR(128) NULL AFTER commit_sha,
  ADD COLUMN s2_postprocess_status VARCHAR(16) NOT NULL DEFAULT 'skipped' AFTER version_tag,
  ADD COLUMN notify_fingerprint VARCHAR(255) NULL AFTER s2_postprocess_status;

CREATE UNIQUE INDEX uk_platform_task_business_key ON platform_task (business_key);
CREATE INDEX idx_platform_task_scan_mode ON platform_task (scan_mode);
CREATE INDEX idx_platform_task_commit_sha ON platform_task (commit_sha);
CREATE INDEX idx_platform_task_repo_name ON platform_task (repo_name);
