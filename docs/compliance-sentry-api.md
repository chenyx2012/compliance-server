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

---

## 鉴权流程

compliance-sentry 使用 **JWT Bearer Token** 鉴权，所有需要登录的接口均要在请求头携带：

```
Authorization: Bearer <access_token>
```

网关会将该请求头**原样透传**给 sentry，无需在网关侧做任何额外处理。

### 第一步：登录获取 Token

```http
POST /platform/compliance-sentry/v1/auth/login
Content-Type: application/x-www-form-urlencoded

username=admin&password=your_password
```

> 注意：登录接口使用 `application/x-www-form-urlencoded`，不是 JSON。

**响应：**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### 第二步：后续请求带 Token

```javascript
// JavaScript 示例
const loginRes = await fetch('/platform/compliance-sentry/v1/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({ username: 'admin', password: 'your_password' })
})
const { access_token } = await loginRes.json()

// 后续所有请求带上 Authorization 头
const res = await fetch('/platform/compliance-sentry/v1/analysis/tasks', {
  headers: { 'Authorization': `Bearer ${access_token}` }
})
```

---

## 平台任务总入口（`POST /platform/tasks`）

文件入库 + 可选触发 compliance-sentry 扫描的统一入口，支持同步与异步两种模式。

**Content-Type**：`multipart/form-data`

**请求头**：需携带 `Authorization: Bearer <token>`

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_name` | string | 是 | 任务/项目名称 |
| `service` | string | 否 | `none`（默认，仅入库目录树）或 `compliance-sentry`（额外提交扫描） |
| `async_scan` | boolean | 否 | `false`（默认，同步等待 sentry 返回）或 `true`（Celery 异步，立即返回 `platform_task_id`） |
| `source_url` | string | 二选一 | Git 仓库地址（带 `.git` 或不带均可） |
| `file` | file | 二选一 | 上传文件（zip/tar.gz/tgz 或普通文件） |
| `third_party` | boolean | 否 | 是否启用第三方依赖扫描，透传给 sentry |
| `fallback_tree` | boolean | 否 | 是否启用 fallback-tree 解析，透传给 sentry |
| `branch_tag` | string | 否 | Git 分支名（仅 git 任务有效） |

### 示例：上传 zip 文件并同步扫描

```javascript
const form = new FormData()
form.append('project_name', 'my-project')
form.append('service', 'compliance-sentry')
form.append('async_scan', 'false')
form.append('file', zipFile)  // File 对象

const res = await fetch('/platform/tasks', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${access_token}` },
  body: form
})
const data = await res.json()
// data.ingest_id       — 目录树入库 ID
// data.sentry.body.analysis_id — sentry 分析任务 ID
```

### 示例：Git 地址异步扫描

```javascript
const form = new FormData()
form.append('project_name', 'my-project')
form.append('service', 'compliance-sentry')
form.append('async_scan', 'true')
form.append('source_url', 'https://github.com/owner/repo.git')

const res = await fetch('/platform/tasks', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${access_token}` },
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
  "meta": { "source": "upload", "type": "archive", "filename": "project.zip", "s3_upload": "Success" },
  "tree": { "path": "project", "next": { ... }, "content": null },
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
  "meta": { ... },
  "tree": { ... },
  "service": "compliance-sentry",
  "sentry_async": true,
  "platform_task_id": "celery-task-uuid"
}
```

---

## 异步任务结果查询（`GET /platform/tasks/result/{task_id}`）

查询 `async_scan=true` 时返回的 `platform_task_id` 对应的 Celery 任务状态。

```http
GET /platform/tasks/result/celery-task-uuid
```

**响应：**

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
    "sentry": { "analysis_id": "uuid-xxxx", "message": "..." }
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

请求体、Query 参数、`Authorization` 等请求头**原样透传**，响应状态码与 body **原样返回**。

---

### 认证模块 /auth

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/platform/compliance-sentry/v1/auth/register` | 注册新用户 |
| POST | `/platform/compliance-sentry/v1/auth/login` | 登录，返回 JWT Token（`application/x-www-form-urlencoded`） |
| PUT | `/platform/compliance-sentry/v1/auth/change-password` | 修改当前用户密码 |
| PUT | `/platform/compliance-sentry/v1/auth/admin/change-password` | 管理员修改任意用户密码 |

---

### 用户管理模块 /users

> 需要 `Authorization` 头

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/compliance-sentry/v1/users/me` | 获取当前用户信息 |
| GET | `/platform/compliance-sentry/v1/users/all` | 获取用户列表（管理员） |
| PUT | `/platform/compliance-sentry/v1/users/{user_id}` | 更新用户信息 |
| DELETE | `/platform/compliance-sentry/v1/users/{user_id}` | 删除用户（管理员） |

---

### 分析任务模块 /analysis /mission /analyze

> 需要 `Authorization` 头

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/compliance-sentry/v1/analysis/tasks` | 任务列表（支持分页与筛选） |
| POST | `/platform/compliance-sentry/v1/mission` | 提交系统任务（管理员，支持 shadow 文件，`multipart/form-data`） |
| POST | `/platform/compliance-sentry/v1/mission/upload` | 上传 ZIP 提交应用级检测（`multipart/form-data`） |
| POST | `/platform/compliance-sentry/v1/mission/git` | 通过 Git URL 提交应用级检测（`multipart/form-data`） |
| POST | `/platform/compliance-sentry/v1/analyze/client` | 提交预处理 JSON 数据启动分析 |
| DELETE | `/platform/compliance-sentry/v1/analysis/{analysis_id}` | 删除分析任务 |
| POST | `/platform/compliance-sentry/v1/analysis/{analysis_id}/terminate` | 终止运行中的任务 |
| POST | `/platform/compliance-sentry/v1/analysis/{analysis_id}/retry` | 重试失败/终止的任务 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/status` | 获取任务状态与进度 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/parameters` | 获取任务创建参数与源码缓存状态 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/report` | 获取分析报告摘要（JSON/PDF/HTML，`?format=json`） |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/report/{report_type}` | 下载报告文件（`dependency_graph`/`license_map`/`compatible_graph`/`final_result`） |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/dependencies` | 获取依赖关系图（数据库版） |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph` | 获取依赖图节点与边（文件版，`?run_id=`） |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/skeleton` | 获取依赖图骨架结构 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/node-metadata` | 获取依赖图节点属性 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/edge-metadata` | 获取依赖图边属性 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/compatibility-results` | 获取压缩的兼容性检查结果 |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/intermediate/license-map` | 获取中间产物（许可证映射/ScanCode 结果） |
| GET | `/platform/compliance-sentry/v1/analysis/{analysis_id}/conflicts` | 获取许可证冲突列表 |
| GET | `/platform/compliance-sentry/v1/mission/{mission_id}/metrics/latest` | 获取任务资源监控最新数据（CPU/内存，`?metrics=cpu,memory`） |

**任务状态轮询示例：**

```javascript
// 提交任务后拿到 analysis_id，轮询状态
const pollStatus = async (analysisId) => {
  const res = await fetch(
    `/platform/compliance-sentry/v1/analysis/${analysisId}/status`,
    { headers: { 'Authorization': `Bearer ${access_token}` } }
  )
  const data = await res.json()
  // data.data.current_status: pending / running / completed / failed / terminated
  // data.data.progress: 0~100
  return data
}
```

---

### 知识库模块 /kb

> 需要 `Authorization` 头

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/compliance-sentry/v1/kb/licenses` | 获取所有许可证列表（仅名称） |
| GET | `/platform/compliance-sentry/v1/kb/licenses/{spdx_id}` | 获取特定许可证详情 |
| PUT | `/platform/compliance-sentry/v1/kb/licenses/{spdx_id}` | 修改许可证信息 |
| DELETE | `/platform/compliance-sentry/v1/kb/licenses/{spdx_id}` | 删除许可证（软删除，管理员） |
| POST | `/platform/compliance-sentry/v1/kb/licenses/compatibility` | 检查许可证兼容性 |
| POST | `/platform/compliance-sentry/v1/kb/licenses/upload` | 批量上传许可证文件 |
| GET | `/platform/compliance-sentry/v1/kb/compatibility/{license_a}/{license_b}` | 查询两个许可证的兼容性 |
| GET | `/platform/compliance-sentry/v1/kb/compatibility/{license_id}/all` | 获取许可证的所有兼容性关系 |
| GET | `/platform/compliance-sentry/v1/kb/compatibility/matrix` | 获取完整的兼容性矩阵 |
| POST | `/platform/compliance-sentry/v1/kb/admin/initialize` | 初始化知识库（管理员） |

---

### 仪表盘模块 /dashboard

> 需要 `Authorization` 头

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/compliance-sentry/v1/dashboard/overview` | 系统总览 |
| GET | `/platform/compliance-sentry/v1/dashboard/task-stats` | 最近 7 天任务统计 |
| GET | `/platform/compliance-sentry/v1/dashboard/license-distribution` | 许可证分布统计（`?limit=8`） |
| GET | `/platform/compliance-sentry/v1/dashboard/system-resources` | 当前系统资源使用率（CPU/内存） |
| GET | `/platform/compliance-sentry/v1/dashboard/task-status-distribution` | 任务状态分布 |
| GET | `/platform/compliance-sentry/v1/dashboard/daily-summary` | 当天任务统计 |

---

### 系统模块 /system

> 需要 `Authorization` 头（`task-limits` 需管理员权限）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/compliance-sentry/v1/system/health` | 系统健康状态 |
| GET | `/platform/compliance-sentry/v1/system/task-limits` | 获取任务并发限制配置（管理员） |
| PUT | `/platform/compliance-sentry/v1/system/task-limits` | 更新任务并发限制配置（管理员） |

---

### 冲突搜索模块 /conflicts

> 需要 `Authorization` 头

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/compliance-sentry/v1/conflicts/search` | 搜索许可证冲突（支持 Query 参数筛选） |

---

### 任务资产模块 /tasks

> 需要 `Authorization` 头

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/platform/compliance-sentry/v1/tasks/{task_id}/files` | 上传/替换 shadow_file 与 license_shadow（`multipart/form-data`） |
| GET | `/platform/compliance-sentry/v1/tasks/{task_id}/files/base64/file_shadow` | 获取 file_shadow 的 Base64 |
| GET | `/platform/compliance-sentry/v1/tasks/{task_id}/files/base64/license_shadow` | 获取 license_shadow 的 Base64 |
| GET | `/platform/compliance-sentry/v1/tasks/{task_id}/files/base64/config` | 获取 config 的 Base64 |
| GET | `/platform/compliance-sentry/v1/tasks/{task_id}/keys` | 获取任务键数组 |
| DELETE | `/platform/compliance-sentry/v1/tasks/{task_id}/keys/{key}` | 删除任务键数组中的一个键 |

---

## 配置项

在项目根目录 `.env` 文件中配置：

```env
# compliance-sentry-main 后端根地址（不含 /api/v1）
COMPLIANCE_SENTRY_BASE_URL=http://127.0.0.1:8010
```

> 注意：sentry 服务端口（默认 8010）不能与网关端口（默认 8000）相同。
