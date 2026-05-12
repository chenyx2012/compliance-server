"""
OAT (Open source Audit Tool) 扫描服务。

将 tools/oat_python 作为 S1 扫描服务集成到平台，不修改 oat_python 任何代码。

调用机制：
  通过 subprocess 以 PYTHONPATH 指向 oat_python/src 来执行 `python -m oat`，
  无需将 oat_python 安装为系统包，完全隔离、不侵入三方库。

规则叠加逻辑（与 oat_python loader.py 一致）：
  内置 OAT-Default.xml（oat_python 自带）
    ↓  通过 -oatconfig 叠加
  自定义规则 XML（来自 oat_rule_config 表，写入临时文件）
    ↓  若项目根有 OAT.xml 则再叠加（oat_python 自动处理）
  最终生效规则

扫描结果：
  解析 PlainReport_<task_name>.txt，结构化写入 oat_scan_result 表，
  并同步更新 platform_task.s1_status。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

# oat_python 的 Python 包所在目录（tools/oat_python/src/oat/__init__.py）
_OAT_SRC_DIR = Path(__file__).parent.parent / "tools" / "oat_python" / "src"

# PlainReport_*.txt 截断阈值（64 KB）
_REPORT_MAX_BYTES = 64 * 1024


# ===========================================================================
# 报告解析
# ===========================================================================

def _parse_report_stats(report_text: str) -> Dict[str, int]:
    """从 PlainReport_*.txt 中提取三类 issue 计数。"""
    def _find_count(pattern: str) -> int:
        m = re.search(pattern, report_text, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    invalid_file_type = _find_count(r"Invalid File Type Total Count:\s*(\d+)")
    license_invalid = _find_count(r"License Header Invalid Total Count:\s*(\d+)")
    copyright_invalid = _find_count(r"Copyright Header Invalid Total Count:\s*(\d+)")
    return {
        "invalid_file_type_count": invalid_file_type,
        "license_header_invalid_count": license_invalid,
        "copyright_header_invalid_count": copyright_invalid,
        "total_issues": invalid_file_type + license_invalid + copyright_invalid,
    }


# ===========================================================================
# 核心扫描函数（异步，供 FastAPI 同步路径使用）
# ===========================================================================

async def run_oat_scan(
    source_dir: Path,
    project_name: str,
    *,
    rule_xml_content: Optional[str] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """
    对 source_dir 执行 OAT 扫描，返回结构化结果。

    参数：
    - source_dir       : 待扫描的源码根目录（本地路径，已存在）
    - project_name     : 项目名称（用于报告文件名和 oat -n 参数）
    - rule_xml_content : 自定义规则 XML 内容；None 表示仅使用 oat_python 内置默认规则
    - timeout          : 最大等待秒数（默认 600s）

    返回 dict 包含：
    - exit_code, total_issues, invalid_file_type_count,
      license_header_invalid_count, copyright_header_invalid_count,
      report_text, stdout_tail, success
    """
    work_dir = Path(tempfile.mkdtemp(prefix="oat_work_"))
    try:
        report_dir = work_dir / "report"
        report_dir.mkdir()

        cmd = [
            sys.executable, "-m", "oat",
            "-s", str(source_dir),
            "-r", str(report_dir),
            "-n", project_name,
            "-ignorePrjOAT",  # 不加载项目根 OAT.xml，避免与平台自定义规则冲突
        ]

        if rule_xml_content:
            rule_xml_path = work_dir / "custom_rule.xml"
            rule_xml_path.write_text(rule_xml_content, encoding="utf-8")
            cmd += ["-oatconfig", str(rule_xml_path)]
            logger.info(
                "run_oat_scan — using custom rule XML (%d chars) — project=%s",
                len(rule_xml_content), project_name,
            )
        else:
            logger.info(
                "run_oat_scan — using builtin default rules — project=%s", project_name
            )

        env = {**os.environ, "PYTHONPATH": str(_OAT_SRC_DIR)}

        logger.info(
            "run_oat_scan start — project=%s src=%s cmd=%s",
            project_name, source_dir, " ".join(cmd),
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error(
                "run_oat_scan timeout — project=%s timeout=%ds", project_name, timeout
            )
            return {
                "exit_code": -1,
                "total_issues": 0,
                "invalid_file_type_count": 0,
                "license_header_invalid_count": 0,
                "copyright_header_invalid_count": 0,
                "report_text": "",
                "stdout_tail": "",
                "success": False,
                "error": f"OAT scan timeout after {timeout}s",
            }

        exit_code = proc.returncode
        stdout_str = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stdout_tail = stdout_str[-3000:] if len(stdout_str) > 3000 else stdout_str

        report_file = report_dir / f"PlainReport_{project_name}.txt"
        report_text = ""
        if report_file.exists():
            raw = report_file.read_bytes()
            report_text = raw[:_REPORT_MAX_BYTES].decode("utf-8", errors="replace")

        stats = _parse_report_stats(report_text)

        logger.info(
            "run_oat_scan done — project=%s exit_code=%d total_issues=%d",
            project_name, exit_code, stats["total_issues"],
        )

        return {
            "exit_code": exit_code,
            "success": exit_code is not None and exit_code >= 0,
            "stdout_tail": stdout_tail,
            "report_text": report_text,
            **stats,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ===========================================================================
# 同步 DB 工具（Celery worker 上下文）
# ===========================================================================

def _update_s1_status_sync(
    platform_task_id: str,
    s1_status: str,
) -> None:
    """同步更新 platform_task.s1_status 并推导 task_status（Celery worker 用）。"""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.models.platform_task import PlatformTask, derive_task_status

    db_url = settings.database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        with Session() as session:
            pt = session.execute(
                select(PlatformTask).where(PlatformTask.task_id == platform_task_id)
            ).scalar_one_or_none()
            if pt is None:
                logger.warning(
                    "_update_s1_status_sync — platform_task_id=%s not found", platform_task_id
                )
                return
            pt.s1_status = s1_status
            pt.task_status = derive_task_status(pt)
            pt.updated_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(
                "_update_s1_status_sync — platform_task_id=%s s1=%s task_status=%s",
                platform_task_id, s1_status, pt.task_status,
            )
    except Exception as exc:
        logger.error(
            "_update_s1_status_sync — failed platform_task_id=%s: %s", platform_task_id, exc
        )
    finally:
        engine.dispose()


def _create_oat_scan_result_running_sync(
    platform_task_id: str,
    rule_config_id: Optional[int],
    celery_task_id: Optional[str] = None,
) -> Optional[int]:
    """
    扫描开始时插入一条 status='running' 的 oat_scan_result 记录，
    返回新记录的 id（供后续 update 使用）。Celery worker 用。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.oat_scan_result import OatScanResult

    db_url = settings.database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        with Session() as session:
            row = OatScanResult(
                platform_task_id=platform_task_id,
                rule_config_id=rule_config_id,
                celery_task_id=celery_task_id,
                status="running",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            logger.info(
                "_create_oat_scan_result_running_sync — id=%d platform_task_id=%s celery_task_id=%s",
                row.id, platform_task_id, celery_task_id,
            )
            return row.id
    except Exception as exc:
        logger.error(
            "_create_oat_scan_result_running_sync — failed platform_task_id=%s: %s",
            platform_task_id, exc,
        )
        return None
    finally:
        engine.dispose()


def _finish_oat_scan_result_sync(
    result_id: Optional[int],
    platform_task_id: str,
    status: str,
    exit_code: Optional[int],
    stats: Dict[str, Any],
    report_text: str,
    error_message: Optional[str],
) -> None:
    """
    扫描完成时，按 result_id 更新已有 oat_scan_result 记录为终态。
    若 result_id 为 None（首次插入失败），则降级为新建一条记录。
    Celery worker 用。
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.models.oat_scan_result import OatScanResult

    db_url = settings.database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        with Session() as session:
            row: Optional[OatScanResult] = None
            if result_id is not None:
                row = session.execute(
                    select(OatScanResult).where(OatScanResult.id == result_id)
                ).scalar_one_or_none()

            if row is None:
                row = OatScanResult(platform_task_id=platform_task_id)
                session.add(row)

            row.status = status
            row.exit_code = exit_code
            row.total_issues = stats.get("total_issues", 0)
            row.invalid_file_type_count = stats.get("invalid_file_type_count", 0)
            row.license_header_invalid_count = stats.get("license_header_invalid_count", 0)
            row.copyright_header_invalid_count = stats.get("copyright_header_invalid_count", 0)
            row.report_text = (report_text[:_REPORT_MAX_BYTES] if report_text else None)
            row.error_message = error_message
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(
                "_finish_oat_scan_result_sync — id=%s platform_task_id=%s status=%s total_issues=%d",
                row.id, platform_task_id, status, stats.get("total_issues", 0),
            )
    except Exception as exc:
        logger.error(
            "_finish_oat_scan_result_sync — failed platform_task_id=%s: %s",
            platform_task_id, exc,
        )
    finally:
        engine.dispose()


def _get_rule_xml_sync(rule_config_id: Optional[int]) -> Optional[str]:
    """同步从 DB 读取规则 XML 内容（Celery worker 用）。"""
    if rule_config_id is None:
        return None
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.models.oat_rule_config import OatRuleConfig

    db_url = settings.database_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        with Session() as session:
            cfg = session.execute(
                select(OatRuleConfig).where(OatRuleConfig.id == rule_config_id)
            ).scalar_one_or_none()
            return cfg.xml_content if cfg else None
    except Exception as exc:
        logger.error("_get_rule_xml_sync — rule_config_id=%s error=%s", rule_config_id, exc)
        return None
    finally:
        engine.dispose()


# ===========================================================================
# 同步 OAT 执行（Celery worker 上下文，阻塞式）
# ===========================================================================

def _run_oat_scan_sync(
    source_dir: Path,
    project_name: str,
    *,
    rule_xml_content: Optional[str] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """
    同步版 OAT 扫描（供 Celery task 使用）。
    与 run_oat_scan 逻辑一致，但用 subprocess.run 替代 asyncio.
    """
    import subprocess

    work_dir = Path(tempfile.mkdtemp(prefix="oat_work_"))
    try:
        report_dir = work_dir / "report"
        report_dir.mkdir()

        cmd = [
            sys.executable, "-m", "oat",
            "-s", str(source_dir),
            "-r", str(report_dir),
            "-n", project_name,
            "-ignorePrjOAT",
        ]

        if rule_xml_content:
            rule_xml_path = work_dir / "custom_rule.xml"
            rule_xml_path.write_text(rule_xml_content, encoding="utf-8")
            cmd += ["-oatconfig", str(rule_xml_path)]

        env = {**os.environ, "PYTHONPATH": str(_OAT_SRC_DIR)}

        logger.info(
            "_run_oat_scan_sync start — project=%s src=%s", project_name, source_dir
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            exit_code = result.returncode
            stdout_str = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            logger.error(
                "_run_oat_scan_sync timeout — project=%s timeout=%ds", project_name, timeout
            )
            return {
                "exit_code": -1,
                "total_issues": 0,
                "invalid_file_type_count": 0,
                "license_header_invalid_count": 0,
                "copyright_header_invalid_count": 0,
                "report_text": "",
                "stdout_tail": "",
                "success": False,
                "error": f"OAT scan timeout after {timeout}s",
            }

        stdout_tail = stdout_str[-3000:] if len(stdout_str) > 3000 else stdout_str

        report_file = report_dir / f"PlainReport_{project_name}.txt"
        report_text = ""
        if report_file.exists():
            raw = report_file.read_bytes()
            report_text = raw[:_REPORT_MAX_BYTES].decode("utf-8", errors="replace")

        stats = _parse_report_stats(report_text)
        logger.info(
            "_run_oat_scan_sync done — project=%s exit_code=%d total_issues=%d",
            project_name, exit_code, stats["total_issues"],
        )
        return {
            "exit_code": exit_code,
            "success": exit_code >= 0,
            "stdout_tail": stdout_tail,
            "report_text": report_text,
            **stats,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ===========================================================================
# Celery 异步任务
# ===========================================================================

@celery_app.task(bind=True, name="platform.oat_scan")
def oat_scan_task(
    self,
    mode: str,
    project_name: str,
    platform_task_id: str,
    rule_config_id: Optional[int] = None,
    temp_zip_path: Optional[str] = None,
    git_url: Optional[str] = None,
    branch_tag: Optional[str] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """
    Celery 异步 OAT 扫描任务。

    mode:
      "upload" — 从 temp_zip_path 解压源码后扫描，完成后删除临时文件
      "git"    — 从 git_url 浅克隆后扫描

    状态流转（platform_task.s1_status）：
      pending → running（任务启动时立即写库）
             → success（扫描完成且 oat 无异常）
             → failed（扫描超时 / 异常 / oat 返回错误）

    流程：
      1. 立即更新 platform_task.s1_status = "running"
      2. 插入 oat_scan_result（status=running，记录 celery_task_id）
      3. 从 oat_rule_config 读取规则 XML（若 rule_config_id 非空）
      4. 准备源码目录（解压 or clone）
      5. 调用 _run_oat_scan_sync 执行扫描
      6. 更新 oat_scan_result 为终态
      7. 更新 platform_task.s1_status 为终态
      8. 清理临时目录/文件
    """
    task_id = self.request.id
    logger.info(
        "oat_scan_task start — task_id=%s mode=%s project=%s platform_task_id=%s rule_config_id=%s",
        task_id, mode, project_name, platform_task_id, rule_config_id,
    )

    # 步骤 1+2：立即标记 running
    _update_s1_status_sync(platform_task_id, "running")
    result_id = _create_oat_scan_result_running_sync(
        platform_task_id=platform_task_id,
        rule_config_id=rule_config_id,
        celery_task_id=task_id,
    )

    rule_xml = _get_rule_xml_sync(rule_config_id)

    src_tmp_dir: Optional[Path] = None
    error_message: Optional[str] = None
    scan_result: Dict[str, Any] = {}

    try:
        src_tmp_dir = Path(tempfile.mkdtemp(prefix="oat_src_"))

        if mode == "upload":
            if not temp_zip_path or not os.path.isfile(temp_zip_path):
                raise FileNotFoundError(f"temp_zip_path not found: {temp_zip_path}")
            _extract_archive_to(Path(temp_zip_path), src_tmp_dir)
            source_dir = _pick_single_root(src_tmp_dir)
        elif mode == "git":
            if not git_url:
                raise ValueError("git_url is required for git mode")
            source_dir = _clone_repo_sync(git_url, src_tmp_dir, branch_tag=branch_tag)
        else:
            raise ValueError(f"unknown mode: {mode!r}")

        scan_result = _run_oat_scan_sync(
            source_dir, project_name, rule_xml_content=rule_xml, timeout=timeout
        )
        scan_error = scan_result.get("error")
        if scan_error:
            error_message = scan_error

        final_status = "success" if not scan_error else "failed"
        _finish_oat_scan_result_sync(
            result_id=result_id,
            platform_task_id=platform_task_id,
            status=final_status,
            exit_code=scan_result.get("exit_code"),
            stats=scan_result,
            report_text=scan_result.get("report_text", ""),
            error_message=error_message,
        )
        _update_s1_status_sync(platform_task_id, final_status)

        logger.info(
            "oat_scan_task done — task_id=%s platform_task_id=%s status=%s total_issues=%d",
            task_id, platform_task_id, final_status, scan_result.get("total_issues", 0),
        )
        return {"status": final_status, "platform_task_id": platform_task_id, **scan_result}

    except Exception as exc:
        error_message = str(exc)
        logger.error(
            "oat_scan_task exception — task_id=%s platform_task_id=%s error=%s",
            task_id, platform_task_id, error_message,
        )
        _finish_oat_scan_result_sync(
            result_id=result_id,
            platform_task_id=platform_task_id,
            status="failed",
            exit_code=None,
            stats={},
            report_text="",
            error_message=error_message[:2000],
        )
        _update_s1_status_sync(platform_task_id, "failed")
        return {"status": "error", "error": error_message, "platform_task_id": platform_task_id}
    finally:
        if src_tmp_dir and src_tmp_dir.exists():
            shutil.rmtree(src_tmp_dir, ignore_errors=True)
        if temp_zip_path and os.path.isfile(temp_zip_path):
            try:
                os.unlink(temp_zip_path)
            except OSError:
                pass


# ===========================================================================
# 内部辅助函数
# ===========================================================================

def _extract_archive_to(archive_path: Path, dest_dir: Path) -> None:
    """解压 zip / tar.gz / tgz 到 dest_dir。"""
    import tarfile
    import zipfile

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
        return
    try:
        if tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(dest_dir)
            return
    except Exception:
        pass
    raise ValueError(f"Unsupported archive format: {archive_path.name}")


def _pick_single_root(extract_dir: Path) -> Path:
    """若解压后只有一个子目录，返回该子目录（与 file_ingest 一致）。"""
    children = [p for p in extract_dir.iterdir() if p.name not in {".DS_Store"}]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_dir


def _clone_repo_sync(
    git_url: str,
    dest_parent: Path,
    branch_tag: Optional[str] = None,
) -> Path:
    """同步浅克隆仓库到 dest_parent/<repo_name>，返回克隆目录。"""
    import subprocess

    repo_name = git_url.rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
    dest = dest_parent / repo_name
    dest.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth", "1", "--single-branch"]
    if branch_tag:
        cmd += ["--branch", branch_tag]
    cmd += [git_url, str(dest)]

    logger.info("_clone_repo_sync — url=%s dest=%s", git_url, dest)
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"git clone failed: {err[:500]}")
    return dest
