# Compliance Gateway（路由网关）

该项目是 **合规平台前端** 的后端路由服务（API 网关）。平台包含 4 个可独立部署的扫描模块服务（A/B/C/D），网关负责：

- 对接前端：统一入口、统一响应格式
- 并发调用下游 4 服务并聚合结果（异步高并发）
- 提供 **队列/多并发能力**：通过 Celery + Redis 承载高并发、耗时扫描请求

## 目录结构

- `app/main.py`：FastAPI 路由（/scan/sync、/scan/async、/scan/result、/files/ingest）
- `app/core/config.py`：配置（四类下游服务 BaseURL / path / 超时等）
- `app/core/celery_app.py`：Celery 实例
- `app/core/http_client.py`：下游 HTTP 调用（通用 POST JSON）
- `app/services/tasks.py`：扫描编排与 Celery 任务（并发调用 A/B/C/D）
- `app/services/file_ingest.py`：文件获取与目录树解析
- `app/schemas/scan.py`：扫描请求/响应模型
- `app/core/database.py`：MySQL 异步连接与会话
- `app/models/file_ingest.py`：文件解析结果表模型（目录树 + meta 入库）

## 依赖

- Python 3.10+
- Redis（用于队列与结果存储）
- MySQL（用于文件目录树解析结果持久化；启动时若不可用仅打日志，不中断）

## 快速开始

以下命令在 **Windows（PowerShell）** 与 **Linux/macOS（Bash）** 下均可用，仅虚拟环境激活方式不同。

### 1) 安装依赖

**Windows (PowerShell)：**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

**Linux / macOS：**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2) 准备配置

```powershell
copy .env.example .env
```

编辑 `.env` 配置 Redis 与 **MySQL**（`MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` 等）。需先创建库：`CREATE DATABASE compliance_gateway CHARACTER SET utf8mb4;`

### 3) 启动 Redis

任选一种方式，保证 `.env` 中 `REDIS_URL` 可连即可：

- **Docker（Windows / Linux 通用）：**

```bash
docker compose up -d
```

- 或本机安装 Redis 并启动服务

### 4) 启动 Celery worker（队列消费者）

**Windows** 下 Celery 不支持默认的 fork 进程池，必须加 `--pool=solo`（或 `--pool=threads`）：

```powershell
celery -A app.core.celery_app:celery_app worker -l INFO -Q compliance_scan --pool=solo
```

**Linux / macOS** 可使用默认池：

```bash
celery -A app.core.celery_app:celery_app worker -l INFO -Q compliance_scan
```

### 5) 启动网关 API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 使用脚本启动（Windows）

在项目根目录、已激活 `.venv` 的前提下，可分别开两个终端执行：

```powershell
.\scripts\start_celery_win.ps1
```

```powershell
.\scripts\start_api_win.ps1
```

脚本会自动切到项目根目录；Celery 脚本已带 `--pool=solo`，无需再改。

## 接口说明

### 1) 同步聚合扫描（适合需要立即结果）

`POST /scan/sync`

请求示例：

```json
{
  "target": "example.com",
  "options": { "level": "deep" },
  "modules": ["a", "b", "c", "d"]
}
```

响应示例（聚合 4 模块结果）：

```json
{
  "target": "example.com",
  "results": [
    { "module": "a", "ok": true, "data": {}, "error": null, "elapsed_ms": 120 }
  ]
}
```

> 网关按配置向每类下游发起 `POST {SERVICE_X_BASE_URL}{SERVICE_X_SCAN_PATH}`，payload 为 `{"target": "...", "options": {...}}`（详见下方下游契约）

### 2) 异步队列扫描（高并发/耗时任务推荐）

`POST /scan/async` 返回 `request_id`（即 Celery task id）

然后轮询：

`GET /scan/result/{request_id}`

### 3) 文件获取与目录树解析（URL / 上传）

`POST /files/ingest`

支持两种方式二选一：

- `source_url`：文件/压缩包 URL（支持 `github` / `gitee` / `gitcode` 的 `blob` 链接，服务端会自动转换为可下载的 raw 链接）
- `file`：上传文件/压缩包（`zip` / `tar` / `tar.gz` / `tgz` 或普通文件）

解析完成后会将 **目录树与 meta 写入 MySQL**（表 `file_ingest_result`），响应中带 `ingest_id`（主键）便于后续按 ID 查询。

返回字段：

- `ok`, `ingest_id`, `meta`, `tree`
- 树节点结构：`path`（节点路径）、`next`（子节点映射）、`content`（目录为 `null`；文件为 JSON，如 `{"text": "..."}` 或 `{"binary": true, "base64": "..."}`）

## 下游服务约定（四模块通用）

网关对 **A/B/C/D 四类服务** 采用同一调用模式，仅通过配置区分，无业务耦合。任一下游（如 compliance-sentry-main 或其它三个服务）只需满足以下约定即可接入。

**请求**

- 方法：`POST`
- URL：`{SERVICE_X_BASE_URL}{SERVICE_X_SCAN_PATH}`（X 为 a/b/c/d，每类独立配置）
- 请求体：JSON `{"target": "<扫描目标>", "options": {<前端透传的可选参数>}}`
- 请求头：同步调用（`/scan/sync`）时，网关会透传前端的 `Authorization`（若存在）；异步任务无请求上下文，不传。

**响应**

- 成功：HTTP 2xx，响应体为任意 JSON，网关原样放入聚合结果的 `data` 字段
- 失败：非 2xx 或异常时，网关记录 `ok: false` 与 `error` 信息，不中断其它模块

**配置项（.env）**

| 含义 | 变量示例 | 默认 |
|------|----------|------|
| 服务 A 根地址 | `SERVICE_A_BASE_URL` | `http://127.0.0.1:9001` |
| 服务 A 扫描 path | `SERVICE_A_SCAN_PATH` | `/scan` |
| 服务 A 单独超时（秒） | `SERVICE_A_TIMEOUT_SECONDS` | 不设则用全局 `UPSTREAM_TIMEOUT_SECONDS` |

B/C/D 同理（`SERVICE_B_*`、`SERVICE_C_*`、`SERVICE_D_*`）。每类服务的 path、端口、超时均可独立配置，便于对接不同实现（如 compliance-sentry-main 可配置为 `/api/v1/...` 的适配端点）。

