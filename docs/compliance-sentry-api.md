# compliance-sentry 接口对接说明

本文档说明合规平台网关（compliance_sever）与 **compliance-sentry-main** 下游服务的完整对接方式，包括鉴权流程、平台任务入口和全量代理接口。

---

## 目录

- [鉴权流程](#鉴权流程)
- [平台任务总入口](#平台任务总入口post-platformtasks)
- [异步任务结果查询](#异步任务结果查询get-platformtasksresulttask_id)
- [全量接口代理](#全量接口代理)
  - [认证模块 /auth](#认证模块-auth)
  - [用户管理模块 /users](#用户管理模块-users)
  - [分析任务模块 /analysis /mission /analyze](#分析任务模块-analysis-mission-analyze)
  - [知识库模块 /kb](#知识库模块-kb)
  - [仪表盘模块 /dashboard](#仪表盘模块-dashboard)
  - [系统模块 /system](#系统模块-system)
  - [冲突搜索模块 /conflicts](#冲突搜索模块-conflicts)
  - [任务资产模块 /tasks](#任务资产模块-tasks)
- [配置项](#配置项)
- [错误响应](#错误响应)

---

## 鉴权流程

**前端无需处理任何鉴权**。网关持有一个服务账号（配置在 `.env` 中），在调用 sentry 的每个接口前自动登录获取 token 并注入请求头，token 过期时自动刷新，对前端完全透明。

> **例外**：认证模块（`/auth/*`）接口使用 `proxy_to_sentry_noauth`，网关不注入服务账号 token，直接透传前端请求（前端用自己账号登录/改密）。

### 配置服务账号（`.env`）

```env
COMPLIANCE_SENTRY_BASE_URL=http://127.0.0.1:3010
COMPLIANCE_SENTRY_USERNAME=admin
COMPLIANCE_SENTRY_PASSWORD=your_sentry_password
# 可选：访问 sentry 的 HTTP 代理，留空则直连
COMPLIANCE_SENTRY_PROXY=
```

### 鉴权工作原理

```
前端请求（无需 Authorization 头）
        │
        ▼
网关 sentry_proxy.py
        │  1. 调用 sentry_auth.get_token()
        │     - 首次：用服务账号登录 sentry，缓存 token（按服务端 expires_in）
        │     - token 距过期不足 60s：自动预刷新
        │  2. 注入 Authorization: Bearer <token>
        │  3. 若 sentry 返回 401：强制刷新 token 后重试一次
        ▼
compliance-sentry-main（完成 JWT 校验）
```

### 前端调用示例

前端直接调用接口，**不需要**登录步骤，**不需要**传 Authorization 头：

```javascript
// 直接查询任务列表，无需任何鉴权处理
const res = await fetch('/platform/compliance-sentry/v1/analysis/tasks')
const data = await res.json()

// 提交平台任务，同样无需 Authorization
const form = new FormData()
form.append('project_name', 'my-project')
form.append('services', 'S3')
form.append('file', zipFile)
const taskRes = await fetch('/platform/tasks', {
  method: 'POST',
  body: form
})
```

---

## 平台任务总入口（`POST /platform/tasks`）

文件入库 + 可选触发 compliance-sentry 扫描的统一入口，支持同步与异步两种模式。

**Content-Type**：`multipart/form-data`

**请求头**：无需携带 `Authorization`，网关自动处理鉴权

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_name` | string | 是 | 任务/项目名称（提交 sentry 时作为 mission 名称） |
| `services` | string[] | 是 | `S1`/`S2`/`S3`/`S4` 多选；其中 `S3`=compliance-sentry |
| `async_scan` | boolean | 否 | `false`（默认，同步等待 sentry 返回）或 `true`（Celery 异步，立即返回 `platform_task_id`） |
| `source_url` | string | 二选一 | Git 仓库地址（与 `file` 二选一） |
| `file` | file | 二选一 | 上传文件（zip/tar.gz/tgz；提交 sentry 时必须为 zip/tar.gz） |
| `third_party` | boolean | 否 | 是否启用第三方依赖扫描，透传给 sentry，默认 `false` |
| `fallback_tree` | boolean | 否 | 是否启用 fallback-tree 解析，透传给 sentry，默认 `false` |
| `branch_tag` | string | 否 | Git 分支或 tag 名（仅 git 任务有效） |
| `shadow_file` | file | 否 | compliance-sentry mission 的 shadow 文件 |
| `license_shadow` | file | 否 | compliance-sentry mission 的 license shadow 文件 |

### 示例：上传 zip 文件并同步扫描

```javascript
const form = new FormData()
form.append('project_name', 'my-project')
form.append('services', 'S3')
form.append('async_scan', 'false')
form.append('file', zipFile)  // File 对象

const res = await fetch('/platform/tasks', {
  method: 'POST',
  body: form   // 无需 Authorization 头，网关自动鉴权
})
const data = await res.json()
// data.ingest_id                    — 目录树入库 ID
// data.sentry.body.analysis_id      — sentry 分析任务 ID
```

### 示例：Git 地址异步扫描

```javascript
const form = new FormData()
form.append('project_name', 'my-project')
form.append('services', 'S3')
form.append('async_scan', 'true')
form.append('source_url', 'https://github.com/owner/repo.git')

const res = await fetch('/platform/tasks', {
  method: 'POST',
  body: form
})
const data = await res.json()
// data.platform_task_id — 用于轮询的 Celery task ID
```

### 响应结构

**同步模式（`async_scan=false`）：**

```json
{
  "ok": true,
  "ingest_id": 42,
  "meta": {
    "source": "upload",
    "type": "archive",
    "filename": "project.zip",
    "s3_upload": "Success"
  },
  "tree": { "path": "project", "next": {}, "content": null },
  "services": ["S3"],
  "service": "compliance-sentry",
  "sentry": {
    "status_code": 202,
    "body": {
      "analysis_id": "uuid-xxxx",
      "message": "Zip uploaded, analysis job accepted and is pending execution."
    }
  }
}
```

**异步模式（`async_scan=true`）：**

```json
{
  "ok": true,
  "ingest_id": 42,
  "meta": { "..." : "..." },
  "tree": { "..." : "..." },
  "services": ["S3"],
  "service": "compliance-sentry",
  "sentry_async": true,
  "platform_task_id": "celery-task-uuid"
}
```

---

## 异步任务结果查询（`GET /platform/tasks/result/{task_id}`）

查询 `async_scan=true` 时返回的 `platform_task_id` 对应的 Celery 任务状态。

```http
GET /platform/tasks/result/{platform_task_id}
```

### 路径参数

| 参数 | 说明 |
|------|------|
| `task_id` | `POST /platform/tasks` 异步模式返回的 `platform_task_id` |

### 响应结构

```json
// 进行中
{ "task_id": "xxx", "state": "PENDING" }

// 成功
{
  "task_id": "xxx",
  "state": "SUCCESS",
  "result": {
    "ok": true,
    "status_code": 202,
    "sentry": {
      "analysis_id": "uuid-xxxx",
      "message": "..."
    }
  }
}

// 失败
{ "task_id": "xxx", "state": "FAILURE", "error": "错误信息" }
```

---

## 全量接口代理

所有接口均通过网关透传，前端统一访问：

```
{METHOD} /platform/compliance-sentry/v1/{path}
```

网关转发到：

```
{METHOD} {COMPLIANCE_SENTRY_BASE_URL}/api/v1/{path}
```

Query 参数、请求体原样透传，响应状态码与 body 原样返回。

---

## 认证模块 /auth

> 认证模块接口由前端直接携带凭据请求，网关**不注入**服务账号 token，原样透传。

---

### `POST /platform/compliance-sentry/v1/auth/register`

注册新用户。

**Content-Type**：`application/json`

**请求体：**

```json
{
  "username": "alice",
  "password": "StrongPass123",
  "email": "alice@example.com"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 用户名，全局唯一 |
| `password` | string | 是 | 密码 |
| `email` | string | 否 | 邮箱 |

**响应（201）：**

```json
{
  "message": "User registered successfully",
  "user_id": "uuid-xxxx"
}
```

---

### `POST /platform/compliance-sentry/v1/auth/login`

用户登录，返回 JWT Token。

**Content-Type**：`application/x-www-form-urlencoded`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 用户名 |
| `password` | string | 是 | 密码 |

**示例（curl）：**

```bash
curl -X POST /platform/compliance-sentry/v1/auth/login \
  -d "username=admin&password=yourpassword"
```

**响应（200）：**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 604800,
  "user": {
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "user_id": "uuid-xxxx",
    "created_at": "2025-11-15T10:14:38Z",
    "last_login": "2026-03-19T01:16:59Z",
    "api_quota": null
  }
}
```

> 登录获得的 `access_token` 后续调用需作为 `Authorization: Bearer <token>` 传入（前端自行登录的场景）。

---

### `PUT /platform/compliance-sentry/v1/auth/change-password`

修改当前登录用户的密码。

**请求头**：`Authorization: Bearer <access_token>`

**Content-Type**：`application/json`

**请求体：**

```json
{
  "old_password": "OldPass123",
  "new_password": "NewPass456"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `old_password` | string | 是 | 当前密码 |
| `new_password` | string | 是 | 新密码 |

**响应（200）：**

```json
{ "message": "Password changed successfully" }
```

---

### `PUT /platform/compliance-sentry/v1/auth/admin/change-password`

管理员修改任意用户密码（无需知道旧密码）。

**请求头**：`Authorization: Bearer <admin_token>`

**Content-Type**：`application/json`

**请求体：**

```json
{
  "username": "alice",
  "new_password": "NewPass456"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 目标用户名 |
| `new_password` | string | 是 | 新密码 |

**响应（200）：**

```json
{ "message": "Password changed successfully" }
```

---

## 用户管理模块 /users

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `GET /platform/compliance-sentry/v1/users/me`

获取当前服务账号的用户信息。

**响应（200）：**

```json
{
  "username": "admin",
  "email": "admin@example.com",
  "role": "admin",
  "user_id": "uuid-xxxx",
  "created_at": "2025-11-15T10:14:38Z",
  "last_login": "2026-03-19T01:16:59Z",
  "api_quota": null
}
```

---

### `GET /platform/compliance-sentry/v1/users/all`

获取全部用户列表（管理员权限）。

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | int | 页码，默认 1 |
| `page_size` | int | 每页数量，默认 20 |

**响应（200）：**

```json
{
  "users": [
    {
      "username": "admin",
      "email": "admin@example.com",
      "role": "admin",
      "user_id": "uuid-xxxx",
      "created_at": "2025-11-15T10:14:38Z",
      "last_login": "2026-03-19T01:16:59Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

---

### `PUT /platform/compliance-sentry/v1/users/{user_id}`

更新用户信息。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `user_id` | 目标用户的 UUID |

**Content-Type**：`application/json`

**请求体（可选字段）：**

```json
{
  "email": "new@example.com",
  "role": "user",
  "api_quota": 1000
}
```

**响应（200）：**

```json
{ "message": "User updated successfully" }
```

---

### `DELETE /platform/compliance-sentry/v1/users/{user_id}`

删除用户（管理员权限）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `user_id` | 目标用户的 UUID |

**响应（200）：**

```json
{ "message": "User deleted successfully" }
```

---

## 分析任务模块 /analysis /mission /analyze

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `GET /platform/compliance-sentry/v1/analysis/tasks`

获取分析任务列表，支持分页与状态筛选。

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | int | 页码，默认 1 |
| `page_size` | int | 每页数量，默认 20 |
| `status` | string | 按状态筛选：`pending` / `running` / `completed` / `failed` / `terminated` |
| `project_name` | string | 按项目名模糊搜索 |

**响应（200）：**

```json
{
  "tasks": [
    {
      "analysis_id": "uuid-xxxx",
      "project_name": "my-project",
      "status": "completed",
      "created_at": "2026-03-01T10:00:00Z",
      "updated_at": "2026-03-01T10:30:00Z",
      "progress": 100
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

---

### `POST /platform/compliance-sentry/v1/mission`

提交系统级任务（管理员，支持 shadow 文件）。

**Content-Type**：`multipart/form-data`

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_name` | string | 是 | 项目名称 |
| `shadow_file` | file | 否 | shadow 文件 |
| `license_shadow` | file | 否 | 许可证 shadow 文件 |
| `third_party` | boolean | 否 | 是否扫描第三方依赖 |
| `fallback_tree` | boolean | 否 | 是否启用 fallback-tree |

**响应（202）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "message": "Mission accepted and is pending execution."
}
```

---

### `POST /platform/compliance-sentry/v1/mission/upload`

上传 ZIP 压缩包并提交应用级检测任务。

**Content-Type**：`multipart/form-data`

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_name` | string | 是 | 项目名称 |
| `file` | file | 是 | zip / tar.gz 压缩包 |
| `third_party` | boolean | 否 | 是否扫描第三方依赖，默认 `false` |
| `fallback_tree` | boolean | 否 | 是否启用 fallback-tree，默认 `false` |

**示例：**

```javascript
const form = new FormData()
form.append('project_name', 'my-app')
form.append('file', zipFile)
form.append('third_party', 'false')

const res = await fetch('/platform/compliance-sentry/v1/mission/upload', {
  method: 'POST',
  body: form
})
const data = await res.json()
// data.analysis_id — 后续查询状态使用
```

**响应（202）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "message": "Zip uploaded, analysis job accepted and is pending execution."
}
```

---

### `POST /platform/compliance-sentry/v1/mission/git`

通过 Git 仓库地址提交应用级检测任务。

**Content-Type**：`multipart/form-data`

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_name` | string | 是 | 项目名称 |
| `git_url` | string | 是 | Git 仓库地址（http/https/ssh） |
| `branch_tag` | string | 否 | 分支或 tag，默认拉主分支 |
| `third_party` | boolean | 否 | 是否扫描第三方依赖，默认 `false` |
| `fallback_tree` | boolean | 否 | 是否启用 fallback-tree，默认 `false` |

**示例：**

```javascript
const form = new FormData()
form.append('project_name', 'my-app')
form.append('git_url', 'https://github.com/owner/repo.git')
form.append('branch_tag', 'main')

const res = await fetch('/platform/compliance-sentry/v1/mission/git', {
  method: 'POST',
  body: form
})
```

**响应（202）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "message": "Git repo submitted, analysis job accepted and is pending execution."
}
```

---

### `POST /platform/compliance-sentry/v1/analyze/client`

提交经客户端预处理后的 JSON 数据直接启动分析（跳过文件上传/克隆阶段）。

**Content-Type**：`application/json`

**请求体：**

```json
{
  "project_name": "my-app",
  "dependency_data": { "...": "预处理后的依赖图 JSON" }
}
```

**响应（202）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "message": "Client data accepted, analysis job is pending execution."
}
```

---

### `DELETE /platform/compliance-sentry/v1/analysis/{analysis_id}`

删除分析任务及其所有关联数据。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{ "message": "Analysis task deleted successfully" }
```

---

### `POST /platform/compliance-sentry/v1/analysis/{analysis_id}/terminate`

终止正在运行的分析任务。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{ "message": "Analysis task terminated" }
```

---

### `POST /platform/compliance-sentry/v1/analysis/{analysis_id}/retry`

重试处于 `terminated` 或 `failed` 状态的任务。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（202）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "message": "Analysis task retried successfully"
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/status`

获取分析任务当前状态与进度，用于前端轮询。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**轮询示例：**

```javascript
const pollStatus = async (analysisId) => {
  const res = await fetch(
    `/platform/compliance-sentry/v1/analysis/${analysisId}/status`
  )
  const data = await res.json()
  return data
}

// 每 3 秒轮询一次，直到 completed / failed / terminated
const timer = setInterval(async () => {
  const { data } = await pollStatus(analysisId)
  if (['completed', 'failed', 'terminated'].includes(data.current_status)) {
    clearInterval(timer)
  }
}, 3000)
```

**响应（200）：**

```json
{
  "data": {
    "analysis_id": "uuid-xxxx",
    "project_name": "my-app",
    "current_status": "running",
    "progress": 45,
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-01T10:15:00Z",
    "error_message": null
  }
}
```

| `current_status` 值 | 说明 |
|---------------------|------|
| `pending` | 等待执行 |
| `running` | 正在运行 |
| `completed` | 已完成 |
| `failed` | 执行失败 |
| `terminated` | 已手动终止 |

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/parameters`

获取任务创建时的参数（项目名、git_url 等）及源码缓存状态。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "project_name": "my-app",
  "source_type": "upload",
  "git_url": null,
  "branch_tag": null,
  "third_party": false,
  "fallback_tree": false,
  "cache_status": "cached"
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/report`

获取分析报告摘要，支持多种格式。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `format` | string | `json`（默认）/ `pdf` / `html` |

**响应（200，`format=json`）：**

```json
{
  "analysis_id": "uuid-xxxx",
  "project_name": "my-app",
  "summary": {
    "total_dependencies": 120,
    "license_issues": 3,
    "compatibility_warnings": 5
  },
  "licenses": ["MIT", "Apache-2.0", "GPL-2.0"]
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/report/{report_type}`

下载具体格式的报告文件。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |
| `report_type` | 报告类型（见下表） |

| `report_type` | 说明 |
|---------------|------|
| `dependency_graph` | 依赖图（GML/JSON 文件） |
| `license_map` | 许可证映射文件 |
| `compatible_graph` | 兼容性图 |
| `final_result` | 最终检测结果（ZIP 压缩包） |

**响应**：二进制文件流，Content-Disposition 含文件名。

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/dependencies`

获取依赖关系图（数据库版，结构化数据）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "nodes": [
    { "id": "pkg-a@1.0.0", "license": "MIT", "type": "direct" }
  ],
  "edges": [
    { "source": "my-app", "target": "pkg-a@1.0.0", "type": "depends_on" }
  ]
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph`

获取依赖图节点与边（文件版）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `run_id` | string | 可选，指定具体运行批次 |

**响应（200）：**

```json
{
  "nodes": [ { "id": "...", "label": "...", "metadata": {} } ],
  "edges": [ { "source": "...", "target": "...", "label": "..." } ]
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/skeleton`

获取依赖图的骨架结构（仅节点 ID 与边，不含 metadata，响应体更小）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "nodes": ["pkg-a@1.0.0", "pkg-b@2.0.0"],
  "edges": [["my-app", "pkg-a@1.0.0"], ["pkg-a@1.0.0", "pkg-b@2.0.0"]]
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/node-metadata`

获取依赖图所有节点的详细属性（许可证、类型、版本等）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "nodes": {
    "pkg-a@1.0.0": {
      "license": "MIT",
      "type": "direct",
      "version": "1.0.0",
      "package_manager": "npm"
    }
  }
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/edge-metadata`

获取依赖图所有边的属性（依赖类型、约束等）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "edges": [
    {
      "source": "my-app",
      "target": "pkg-a@1.0.0",
      "dependency_type": "runtime",
      "version_constraint": "^1.0.0"
    }
  ]
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/compatibility-results`

获取压缩后的兼容性检查结果（高效传输大型兼容性矩阵）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "compatibility_matrix": {
    "pkg-a@1.0.0": {
      "pkg-b@2.0.0": "compatible",
      "pkg-c@3.0.0": "incompatible"
    }
  },
  "conflicts": [
    {
      "package_a": "pkg-a@1.0.0",
      "license_a": "MIT",
      "package_b": "pkg-c@3.0.0",
      "license_b": "GPL-3.0",
      "conflict_reason": "Copyleft conflict"
    }
  ]
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/intermediate/license-map`

获取中间产物（ScanCode 扫描结果或许可证映射），用于调试与审计。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "license_map": {
    "pkg-a@1.0.0": {
      "detected_licenses": ["MIT"],
      "source": "scancode",
      "confidence": 0.98
    }
  }
}
```

---

### `GET /platform/compliance-sentry/v1/analysis/{analysis_id}/conflicts`

获取该分析任务中检测到的许可证冲突列表。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `analysis_id` | 分析任务 UUID |

**响应（200）：**

```json
{
  "conflicts": [
    {
      "package_a": "pkg-a@1.0.0",
      "license_a": "MIT",
      "package_b": "pkg-c@3.0.0",
      "license_b": "GPL-3.0",
      "conflict_reason": "Copyleft conflict"
    }
  ],
  "total": 1
}
```

---

### `GET /platform/compliance-sentry/v1/mission/{mission_id}/metrics/latest`

获取任务运行时资源监控的最新一条数据（CPU / 内存）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `mission_id` | mission UUID（与 analysis_id 对应） |

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `metrics` | string | 逗号分隔，如 `cpu,memory`；默认返回全部 |

**响应（200）：**

```json
{
  "timestamp": "2026-03-01T10:15:00Z",
  "cpu_percent": 42.5,
  "memory_mb": 1024.0
}
```

---

## 知识库模块 /kb

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `GET /platform/compliance-sentry/v1/kb/licenses`

获取知识库中所有许可证名称列表。

**响应（200）：**

```json
{
  "licenses": ["MIT", "Apache-2.0", "GPL-2.0-only", "GPL-3.0-only", "BSD-3-Clause"]
}
```

---

### `GET /platform/compliance-sentry/v1/kb/licenses/{spdx_id}`

获取特定许可证的详细信息。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `spdx_id` | SPDX 许可证标识，如 `MIT`、`Apache-2.0` |

**响应（200）：**

```json
{
  "spdx_id": "MIT",
  "name": "MIT License",
  "category": "Permissive",
  "is_osi_approved": true,
  "is_fsf_libre": true,
  "copyleft_type": null,
  "compatible_with": ["Apache-2.0", "GPL-2.0-only"],
  "text_url": "https://spdx.org/licenses/MIT.html"
}
```

---

### `PUT /platform/compliance-sentry/v1/kb/licenses/{spdx_id}`

修改知识库中许可证的信息（管理员）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `spdx_id` | SPDX 许可证标识 |

**Content-Type**：`application/json`

**请求体（可选字段）：**

```json
{
  "category": "Copyleft",
  "copyleft_type": "strong",
  "compatible_with": ["MIT"]
}
```

**响应（200）：**

```json
{ "message": "License updated successfully" }
```

---

### `DELETE /platform/compliance-sentry/v1/kb/licenses/{spdx_id}`

软删除许可证（管理员），不影响历史分析数据。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `spdx_id` | SPDX 许可证标识 |

**响应（200）：**

```json
{ "message": "License deleted successfully" }
```

---

### `POST /platform/compliance-sentry/v1/kb/licenses/compatibility`

检查两个许可证之间的兼容性。

**Content-Type**：`application/json`

**请求体：**

```json
{
  "license_a": "MIT",
  "license_b": "GPL-3.0-only"
}
```

**响应（200）：**

```json
{
  "license_a": "MIT",
  "license_b": "GPL-3.0-only",
  "compatible": false,
  "reason": "GPL-3.0-only has strong copyleft requirements incompatible with MIT sublicensing"
}
```

---

### `POST /platform/compliance-sentry/v1/kb/licenses/upload`

批量上传许可证文件（CSV / JSON 格式）更新知识库。

**Content-Type**：`multipart/form-data`

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | CSV 或 JSON 格式的许可证数据文件 |

**响应（200）：**

```json
{
  "message": "Licenses uploaded successfully",
  "added": 10,
  "updated": 3,
  "skipped": 1
}
```

---

### `GET /platform/compliance-sentry/v1/kb/compatibility/{license_a}/{license_b}`

查询两个许可证的兼容性（路径参数版）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `license_a` | 第一个许可证 SPDX ID |
| `license_b` | 第二个许可证 SPDX ID |

**响应（200）：** 同 `/kb/licenses/compatibility`

---

### `GET /platform/compliance-sentry/v1/kb/compatibility/{license_id}/all`

获取指定许可证与知识库中所有许可证的兼容性关系。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `license_id` | 目标许可证 SPDX ID |

**响应（200）：**

```json
{
  "license_id": "MIT",
  "compatibility": {
    "Apache-2.0": { "compatible": true },
    "GPL-2.0-only": { "compatible": false, "reason": "Copyleft conflict" },
    "BSD-3-Clause": { "compatible": true }
  }
}
```

---

### `GET /platform/compliance-sentry/v1/kb/compatibility/matrix`

获取知识库中所有许可证的完整兼容性矩阵。

**响应（200）：**

```json
{
  "licenses": ["MIT", "Apache-2.0", "GPL-2.0-only"],
  "matrix": {
    "MIT": { "Apache-2.0": true, "GPL-2.0-only": false },
    "Apache-2.0": { "MIT": true, "GPL-2.0-only": false },
    "GPL-2.0-only": { "MIT": false, "Apache-2.0": false }
  }
}
```

---

### `POST /platform/compliance-sentry/v1/kb/admin/initialize`

初始化知识库（管理员），从内置数据集导入基础许可证信息。

**响应（200）：**

```json
{
  "message": "Knowledge base initialized successfully",
  "total_licenses": 500
}
```

---

## 仪表盘模块 /dashboard

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `GET /platform/compliance-sentry/v1/dashboard/overview`

获取系统总览数据（任务总数、完成率、许可证统计等）。

**响应（200）：**

```json
{
  "total_tasks": 256,
  "completed_tasks": 230,
  "failed_tasks": 10,
  "running_tasks": 3,
  "total_licenses_detected": 45,
  "total_conflicts_found": 120
}
```

---

### `GET /platform/compliance-sentry/v1/dashboard/task-stats`

获取最近 7 天的每日任务统计。

**响应（200）：**

```json
{
  "stats": [
    { "date": "2026-03-13", "submitted": 5, "completed": 4, "failed": 1 },
    { "date": "2026-03-14", "submitted": 8, "completed": 8, "failed": 0 }
  ]
}
```

---

### `GET /platform/compliance-sentry/v1/dashboard/license-distribution`

获取所有已完成任务中检测到的许可证分布统计。

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `limit` | int | 返回前 N 个，默认 8 |

**响应（200）：**

```json
{
  "distribution": [
    { "license": "MIT", "count": 980, "percentage": 45.2 },
    { "license": "Apache-2.0", "count": 560, "percentage": 25.8 },
    { "license": "GPL-3.0-only", "count": 320, "percentage": 14.7 }
  ]
}
```

---

### `GET /platform/compliance-sentry/v1/dashboard/system-resources`

获取当前服务器资源使用率（实时快照）。

**响应（200）：**

```json
{
  "timestamp": "2026-03-19T10:00:00Z",
  "cpu_percent": 35.2,
  "memory_total_mb": 16384,
  "memory_used_mb": 8192,
  "memory_percent": 50.0,
  "disk_total_gb": 500,
  "disk_used_gb": 120
}
```

---

### `GET /platform/compliance-sentry/v1/dashboard/task-status-distribution`

获取所有任务按状态的分布统计。

**响应（200）：**

```json
{
  "distribution": {
    "pending": 5,
    "running": 3,
    "completed": 230,
    "failed": 10,
    "terminated": 8
  }
}
```

---

### `GET /platform/compliance-sentry/v1/dashboard/daily-summary`

获取当天的任务统计摘要。

**响应（200）：**

```json
{
  "date": "2026-03-19",
  "submitted": 12,
  "completed": 10,
  "failed": 1,
  "running": 1,
  "avg_duration_minutes": 18.5
}
```

---

## 系统模块 /system

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `GET /platform/compliance-sentry/v1/system/health`

获取系统各组件的健康状态。

**响应（200）：**

```json
{
  "status": "healthy",
  "components": {
    "database": "healthy",
    "redis": "healthy",
    "rabbitmq": "healthy",
    "celery_worker": "healthy"
  },
  "version": "1.0.0",
  "uptime_seconds": 86400
}
```

---

### `GET /platform/compliance-sentry/v1/system/task-limits`

获取当前任务并发限制配置（管理员）。

**响应（200）：**

```json
{
  "max_concurrent_tasks": 10,
  "max_queue_size": 100,
  "task_timeout_seconds": 86400
}
```

---

### `PUT /platform/compliance-sentry/v1/system/task-limits`

更新任务并发限制配置（管理员）。

**Content-Type**：`application/json`

**请求体（可选字段）：**

```json
{
  "max_concurrent_tasks": 5,
  "max_queue_size": 50
}
```

**响应（200）：**

```json
{ "message": "Task limits updated successfully" }
```

---

## 冲突搜索模块 /conflicts

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `GET /platform/compliance-sentry/v1/conflicts/search`

跨任务全局搜索许可证冲突记录。

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `license_a` | string | 第一个许可证 SPDX ID（模糊匹配） |
| `license_b` | string | 第二个许可证 SPDX ID（模糊匹配） |
| `project_name` | string | 按项目名筛选 |
| `page` | int | 页码，默认 1 |
| `page_size` | int | 每页数量，默认 20 |

**示例：**

```http
GET /platform/compliance-sentry/v1/conflicts/search?license_a=GPL&page=1&page_size=10
```

**响应（200）：**

```json
{
  "conflicts": [
    {
      "analysis_id": "uuid-xxxx",
      "project_name": "my-app",
      "package_a": "pkg-a@1.0.0",
      "license_a": "MIT",
      "package_b": "pkg-c@3.0.0",
      "license_b": "GPL-3.0-only",
      "conflict_reason": "Copyleft conflict"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

---

## 任务资产模块 /tasks

> 以下接口网关自动注入服务账号 token，前端无需传 Authorization。

---

### `POST /platform/compliance-sentry/v1/tasks/{task_id}/files`

上传或替换任务的 shadow 文件资产。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 任务 UUID |

**Content-Type**：`multipart/form-data`

**请求参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_shadow` | file | 否 | 文件 shadow（覆盖式上传） |
| `license_shadow` | file | 否 | 许可证 shadow |

**响应（200）：**

```json
{ "message": "Files uploaded successfully" }
```

---

### `GET /platform/compliance-sentry/v1/tasks/{task_id}/files/base64/file_shadow`

获取任务 `file_shadow` 文件的 Base64 编码内容。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 任务 UUID |

**响应（200）：**

```json
{
  "task_id": "uuid-xxxx",
  "filename": "file_shadow",
  "content_base64": "SGVsbG8gV29ybGQ..."
}
```

---

### `GET /platform/compliance-sentry/v1/tasks/{task_id}/files/base64/license_shadow`

获取任务 `license_shadow` 文件的 Base64 编码内容。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 任务 UUID |

**响应（200）：** 同 `file_shadow`，`filename` 为 `license_shadow`。

---

### `GET /platform/compliance-sentry/v1/tasks/{task_id}/files/base64/config`

获取任务 `config` 文件的 Base64 编码内容。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 任务 UUID |

**响应（200）：** 同 `file_shadow`，`filename` 为 `config`。

---

### `GET /platform/compliance-sentry/v1/tasks/{task_id}/keys`

获取任务的键数组（若为空将尝试从检测结果自动加载）。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 任务 UUID |

**响应（200）：**

```json
{
  "task_id": "uuid-xxxx",
  "keys": ["key-a", "key-b", "key-c"]
}
```

---

### `DELETE /platform/compliance-sentry/v1/tasks/{task_id}/keys/{key}`

删除任务键数组中的指定键。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 任务 UUID |
| `key` | 要删除的键名 |

**响应（200）：**

```json
{ "message": "Key deleted successfully" }
```

---

## 配置项

在项目根目录 `.env` 文件中配置：

```env
# compliance-sentry-main 后端根地址（不含 /api/v1）
# docker-compose 端口映射为 3010:8000，与网关同机部署时使用 127.0.0.1:3010
COMPLIANCE_SENTRY_BASE_URL=http://127.0.0.1:3010

# 网关服务账号（代替前端自动登录 sentry，前端无需传 Authorization）
COMPLIANCE_SENTRY_USERNAME=admin
COMPLIANCE_SENTRY_PASSWORD=your_sentry_password

# 可选：访问 sentry 时使用的 HTTP 代理，留空则直连
# 例如 http://127.0.0.1:7890
COMPLIANCE_SENTRY_PROXY=
```

> **注意**：sentry 服务端口（默认 3010）不能与网关端口（默认 8000）相同。

---

## 错误响应

网关层面的错误（连接失败、超时、鉴权失败）统一返回以下格式：

```json
{
  "error": "sentry_connect_error",
  "detail": "连接 compliance-sentry 失败（http://127.0.0.1:3010）：..."
}
```

| HTTP 状态码 | `error` 字段 | 说明 |
|-------------|-------------|------|
| 503 | `sentry_connect_timeout` | 连接超时（10s 内未建立连接），检查 sentry 服务是否启动 |
| 503 | `sentry_connect_error` | 连接被拒绝，检查 `COMPLIANCE_SENTRY_BASE_URL` 与端口 |
| 503 | `sentry_auth_failed` | 网关服务账号登录失败，检查账号密码配置 |
| 504 | `sentry_read_timeout` | sentry 响应超时（120s），任务过重可改用 `async_scan=true` |
| 502 | `sentry_request_error` | 其他请求错误 |

sentry 本身返回的业务错误（4xx / 5xx）原样透传给前端。
