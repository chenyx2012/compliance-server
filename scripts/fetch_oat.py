"""
拉取 S1 扫描依赖的 OAT 仓（HiAgent/compliance）
================================================

OAT 不随本仓提交，首次部署或升级时执行本脚本：
  python scripts/fetch_oat.py            # 目录不存在则浅克隆；已存在则跳过
  python scripts/fetch_oat.py --update   # 拉取最新代码（丢弃该目录内本地改动）

配置（.env，与正式服务一致）：
  OAT_REPO_URL       默认 https://gitcode.com/HiAgent/compliance.git
  OAT_REPO_REF       分支或 tag；留空用远程默认分支
  OAT_REPO_DIR       本地目录，默认 app/tools/compliance（相对项目根）
  OAT_PYTHON_SUBDIR  仓内 Python 版目录，默认 OAT_python

完成后会对一个临时目录跑一次 `python -m oat`，确认 lxml / chardet 等依赖可用。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import settings  # noqa: E402


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def _clone(url: str, ref: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    _git(*cmd, url, str(dest))


def _update(ref: str, dest: Path) -> None:
    _git("fetch", "--depth", "1", "origin", ref or "HEAD", cwd=dest)
    _git("reset", "--hard", "FETCH_HEAD", cwd=dest)


def _smoke_test(src_dir: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="oat_fetch_check_"))
    try:
        sample = work / "src"
        sample.mkdir()
        (sample / "hello.py").write_text("print('hello')\n", encoding="utf-8")
        report = work / "report"
        report.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "oat", "-s", str(sample), "-r", str(report),
             "-n", "fetch_check", "-ignorePrjOAT"],
            env={**os.environ, "PYTHONPATH": str(src_dir)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        # 退出码 0=无问题，1=有问题；其它表示运行失败（多为缺依赖）
        if result.returncode not in (0, 1):
            raise RuntimeError(
                f"OAT smoke test failed (exit={result.returncode}):\n"
                f"{(result.stdout + result.stderr)[-2000:]}\n"
                "Hint: pip install -r requirements.txt（需要 lxml、chardet）"
            )
        if not (report / "PlainReport_fetch_check.txt").is_file():
            raise RuntimeError("OAT smoke test produced no PlainReport_fetch_check.txt")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download OAT (HiAgent/compliance) for S1 scans")
    parser.add_argument("--update", action="store_true", help="已存在时拉取最新代码")
    parser.add_argument("--url", default=settings.oat_repo_url)
    parser.add_argument("--ref", default=settings.oat_repo_ref)
    parser.add_argument("--skip-check", action="store_true", help="跳过下载后的扫描自检")
    args = parser.parse_args()

    dest = settings.oat_repo_path
    src_dir = settings.oat_src_dir

    if (dest / ".git").is_dir():
        if args.update:
            print(f"[fetch_oat] updating {dest} (ref={args.ref or 'default'})")
            _update(args.ref, dest)
        else:
            print(f"[fetch_oat] {dest} already exists, skip (use --update to upgrade)")
    else:
        if dest.exists() and any(dest.iterdir()):
            print(f"[fetch_oat] {dest} exists but is not a git checkout; remove it first", file=sys.stderr)
            return 2
        print(f"[fetch_oat] cloning {args.url} (ref={args.ref or 'default'}) -> {dest}")
        _clone(args.url, args.ref, dest)

    commit = _git("rev-parse", "--short", "HEAD", cwd=dest)
    if not (src_dir / "oat" / "__main__.py").is_file():
        print(f"[fetch_oat] OAT package not found at {src_dir}/oat; "
              "check OAT_PYTHON_SUBDIR", file=sys.stderr)
        return 3

    if not args.skip_check:
        _smoke_test(src_dir)
        print("[fetch_oat] smoke test passed")

    print(f"[fetch_oat] ready: commit={commit} PYTHONPATH={src_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
