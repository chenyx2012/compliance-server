"""
OAT 扫描结果三类 issue JSON 列回填脚本
========================================

功能：
  对 oat_scan_result 表中 report_text 不为空、但
  invalid_file_type_issues / license_header_invalid_issues /
  copyright_header_invalid_issues 为 NULL 的历史记录，
  解析 report_text 并回填三列。

运行方式（项目根目录下）：
  python scripts/backfill_oat_issues.py

可选参数（环境变量）：
  BACKFILL_BATCH_SIZE   每批处理条数，默认 200
  BACKFILL_DRY_RUN      设为 "1" 时只统计、不写库（默认 "0"）
  BACKFILL_LIMIT        最多处理多少条（0 = 不限，默认 0）

配置读取方式与正式服务一致（读取 .env 文件）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── 确保能导入项目包 ────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_oat_issues")

# ── 配置 ─────────────────────────────────────────────────────────────────────
BATCH_SIZE: int = int(os.getenv("BACKFILL_BATCH_SIZE", "200"))
DRY_RUN: bool = os.getenv("BACKFILL_DRY_RUN", "0") == "1"
LIMIT: int = int(os.getenv("BACKFILL_LIMIT", "0"))  # 0 = 不限

# ── 解析逻辑（与 app/services/oat_scanner.py 保持一致）─────────────────────

_ISSUE_LINE_RE = re.compile(
    r"^Name:\t(?P<name>[^\t]+)\t"
    r"Content:\t(?P<content>[^\t]*)\t"
    r"Line:\t\d+\t"
    r"Project:\t(?P<project>[^\t]*)\t"
    r"File:\t(?P<file>.+)$"
)

_ISSUE_TYPE_MAP = {
    "Invalid File Type": "invalid_file_type",
    "License Header Invalid": "license_header_invalid",
    "Copyright Header Invalid": "copyright_header_invalid",
}


def parse_report_issues(report_text: str) -> dict[str, list]:
    """从 PlainReport_*.txt 内容解析三类 issue 列表。"""
    buckets: dict[str, list] = {
        "invalid_file_type": [],
        "license_header_invalid": [],
        "copyright_header_invalid": [],
    }
    for raw_line in report_text.splitlines():
        line = raw_line.strip()
        m = _ISSUE_LINE_RE.match(line)
        if not m:
            continue
        bucket_key = _ISSUE_TYPE_MAP.get(m.group("name"))
        if bucket_key is None:
            continue
        buckets[bucket_key].append(
            {
                "file": m.group("file"),
                "content": m.group("content"),
                "project": m.group("project"),
            }
        )
    return buckets


# ── 数据库连接 ────────────────────────────────────────────────────────────────

def _make_sync_db_url() -> str:
    """从与正式服务相同的配置中读取 MySQL URL，改为同步驱动。"""
    try:
        from app.core.config import settings
        return settings.database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    except Exception as exc:
        logger.warning("无法导入 app.core.config（%s），尝试从环境变量构建 URL", exc)
        from urllib.parse import quote_plus
        host = os.getenv("MYSQL_HOST", "127.0.0.1")
        port = os.getenv("MYSQL_PORT", "3306")
        user = quote_plus(os.getenv("MYSQL_USER", "root"))
        password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
        database = os.getenv("MYSQL_DATABASE", "compliance_gateway")
        charset = os.getenv("MYSQL_CHARSET", "utf8mb4")
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset={charset}"


# ── 核心回填逻辑 ──────────────────────────────────────────────────────────────

def run_backfill() -> None:
    db_url = _make_sync_db_url()
    logger.info("连接数据库: %s", db_url.split("@")[-1])  # 不打印密码

    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    if DRY_RUN:
        logger.info("=== DRY_RUN 模式：仅统计，不写库 ===")

    # ── 统计待处理总数 ────────────────────────────────────────────────────────
    with Session() as session:
        count_sql = text(
            "SELECT COUNT(*) FROM oat_scan_result "
            "WHERE report_text IS NOT NULL "
            "  AND invalid_file_type_issues IS NULL"
        )
        total_pending: int = session.execute(count_sql).scalar_one()

    if total_pending == 0:
        logger.info("没有需要回填的记录，退出。")
        return

    effective_limit = total_pending if LIMIT == 0 else min(LIMIT, total_pending)
    logger.info(
        "待回填记录: %d 条，本次处理上限: %d 条，每批: %d 条",
        total_pending, effective_limit, BATCH_SIZE,
    )

    # ── 分批处理 ──────────────────────────────────────────────────────────────
    processed = 0
    skipped = 0
    errors = 0
    t_start = time.time()

    with Session() as session:
        offset = 0

        while processed < effective_limit:
            batch_limit = min(BATCH_SIZE, effective_limit - processed)

            rows = session.execute(
                text(
                    "SELECT id, report_text "
                    "FROM oat_scan_result "
                    "WHERE report_text IS NOT NULL "
                    "  AND invalid_file_type_issues IS NULL "
                    "ORDER BY id ASC "
                    "LIMIT :lim OFFSET :off"
                ),
                {"lim": batch_limit, "off": offset},
            ).fetchall()

            if not rows:
                break

            batch_updates = []
            for row in rows:
                row_id: int = row[0]
                report_text: str = row[1] or ""

                if not report_text.strip():
                    skipped += 1
                    continue

                try:
                    issues = parse_report_issues(report_text)
                    batch_updates.append(
                        {
                            "row_id": row_id,
                            "ift": json.dumps(issues["invalid_file_type"], ensure_ascii=False),
                            "lic": json.dumps(issues["license_header_invalid"], ensure_ascii=False),
                            "cp": json.dumps(issues["copyright_header_invalid"], ensure_ascii=False),
                        }
                    )
                except Exception as exc:
                    logger.warning("解析失败 id=%d: %s", row_id, exc)
                    errors += 1

            if batch_updates and not DRY_RUN:
                for upd in batch_updates:
                    session.execute(
                        text(
                            "UPDATE oat_scan_result "
                            "SET invalid_file_type_issues        = :ift, "
                            "    license_header_invalid_issues   = :lic, "
                            "    copyright_header_invalid_issues = :cp, "
                            "    updated_at = :now "
                            "WHERE id = :row_id"
                        ),
                        {
                            **upd,
                            "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                session.commit()

            processed += len(rows)
            offset += len(rows)  # 注意：由于 WHERE 过滤条件的变化，offset 在回填完成后不再有效，使用 processed 控制上限即可

            elapsed = time.time() - t_start
            logger.info(
                "进度: %d/%d 条已处理 | 本批 %d 条已更新 | 跳过 %d | 错误 %d | 耗时 %.1f s",
                processed, effective_limit,
                len(batch_updates), skipped, errors, elapsed,
            )

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    total_elapsed = time.time() - t_start
    logger.info(
        "回填完成 | 总处理: %d | 已更新: %d | 空文本跳过: %d | 解析错误: %d | 总耗时: %.1f s",
        processed,
        processed - skipped - errors,
        skipped,
        errors,
        total_elapsed,
    )

    if DRY_RUN:
        logger.info("DRY_RUN 模式，数据库未修改。")


if __name__ == "__main__":
    run_backfill()
