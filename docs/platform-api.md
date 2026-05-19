# 平台自有接口说明

本文档说明合规平台网关（compliance_sever）的**平台自有接口**，包括任务提交、首页看板、任务管理和 OAT 规则配置。

---

## 目录

- [平台任务总入口](#平台任务总入口post-platformtasks)
- **首页看板**
  - [监控项目总数](#监控项目总数get-platformdashboard)
  - [总体风险数](#总体风险数get-platformdashboardrisk-overview)
  - [待处理扫描任务数](#待处理扫描任务数get-platformdashboardpending-risks)
  - [最近 6 个月合规趋势](#最近-6-个月合规趋势get-platformdashboardcompliance-trend)
  - [OAT 风险类型分布（饼图）](#oat-风险类型分布饼图get-platformdashboards1risk-distribution)
- **平台任务管理**
  - [多条件查询](#多条件查询get-platformtasksquery)
  - [查询单条任务](#查询单条任务get-platformtaskstask_id)
  - [服务状态回调](#服务状态回调patch-platformtaskstask_idservice-status)
  - [实时同步 S3 状态](#实时同步-s3-状态post-platformtaskstask_ids3sync-status)
- **OAT 规则配置**
  - [列出规则配置](#列出规则配置get-platformoat-rules)
  - [创建规则配置](#创建规则配置post-platformoat-rules)
  - [查看内置默认 XML](#查看内置默认-xmlget-platformoat-rulesbuiltin-xml)
  - [获取单条规则](#获取单条规则get-platformoat-rulesrule_id)
  - [更新规则配置](#更新规则配置put-platformoat-rulesrule_id)
  - [删除规则配置](#删除规则配置delete-platformoat-rulesrule_id)
- **OAT 扫描结果**
  - [扫描任务列表查询](#扫描任务列表查询get-platformoat-scan-results)
  - [查询单条扫描结果](#查询单条扫描结果get-platformoat-scan-resultstask_id)
  - [取消 S1 扫描](#取消-s1-扫描delete-platformtaskstask_ids1)
- [数据模型参考](#数据模型参考)

---

## 平台任务总入口（`POST /platform/tasks`）

文件入库 + 触发多项扫描服务的统一入口，支持同步与异步两种模式。

**Content-Type**：`multipart/form-data`

### 扫描服务说明

| `services` 值 | 服务 | 说明 |
|--------------|------|------|
| `S1` | OAT 开源合规扫描 | 调用内置 `oat_python`，可配置自定义规则 |
| `S3` | compliance-sentry | 依赖图解析与许可证兼容性检测 |
| `S2` / `S4` | 预留 | 占位，当前 skipped |

### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_name` | string | 是 | 任务/项目名称 |
| `services` | string[] | 是 | 扫描服务多选，多值需多次 `append` 或逗号分隔 |
| `async_scan` | boolean | 否 | `false`（默认，同步等待）或 `true`（Celery 异步，立即返回） |
| `source_url` | string | 二选一 | Git 仓库地址（与 `file` 二选一） |
| `file` | file | 二选一 | 上传 zip/tar.gz/tgz（与 `source_url` 二选一） |
| `third_party` | boolean | 否 | 是否扫描第三方依赖（透传 S3），默认 `false` |
| `fallback_tree` | boolean | 否 | 是否启用 fallback-tree（透传 S3），默认 `false` |
| `branch_tag` | string | 否 | Git 分支或 tag（仅 git 任务有效） |
| `shadow_file` | file | 否 | S3（compliance-sentry）的 shadow 文件 |
| `license_shadow` | file | 否 | S3（compliance-sentry）的 license shadow 文件 |
| `s1_rule_config_id` | integer | 否 | **S1 专用**：OAT 规则配置 ID（来自 `/platform/oat-rules`）；不传则使用 oat_python 内置默认规则 |

### 响应结构

**同步模式（S1 + S3 均成功）：**

```json
{
  "status": "success",
  "platform_task_id": "pt-a1b2c3d4...",
  "ingest_id": "42",
  "meta": { "source": "upload", "type": "archive", "filename": "project.zip" },
  "tree": { "path": "project", "next": {}, "content": null },
  "services": ["S1", "S3"],
  "s1": {
    "status": "success",
    "total_issues": 3,
    "invalid_file_type_count": 0,
    "license_header_invalid_count": 2,
    "copyright_header_invalid_count": 1,
    "invalid_file_type_issues": [],
    "license_header_invalid_issues": [
      { "file": "src/utils.c", "content": "NoLicenseHeader", "project": "my-project" },
      { "file": "src/core.c",  "content": "GPL-2.0-only",    "project": "my-project" }
    ],
    "copyright_header_invalid_issues": [
      { "file": "src/utils.c", "content": "NULL", "project": "my-project" }
    ],
    "rule_config_id": null
  },
  "sentry": {
    "status_code": 202,
    "body": { "analysis_id": "uuid-xxxx", "message": "..." }
  }
}
```

**异步模式（`async_scan=true`）：**

```json
{
  "status": "success",
  "platform_task_id": "pt-a1b2c3d4...",
  "ingest_id": "42",
  "services": ["S1", "S3"],
  "s1_async": true,
  "s1_celery_task_id": "celery-task-uuid",
  "sentry_async": true
}
```

> S3 扫描状态变化：`pending → running → success/failed`。sentry 接受任务后网关立即设为 `running`，后台轮询（或调用 `POST sync-status`）在完成后更新终态。

---

## 监控项目总数（`GET /platform/dashboard`）

本月监控项目数与上月环比。

**统计口径**：`platform_task` 表中 `task_status != 'deleted'` 的记录，按 `created_at` 所在自然月计数。

### 响应（200）

```json
{
  "month": "2026-05",
  "monitor_projects": {
    "current": 42,
    "last_month": 30,
    "change": 12,
    "change_rate": 40.0
  }
}
```

| 字段 | 说明 |
|------|------|
| `monitor_projects.current` | 本月新增监控项目数 |
| `monitor_projects.last_month` | 上月监控项目数 |
| `monitor_projects.change` | 环比变化量（正为增，负为减） |
| `monitor_projects.change_rate` | 环比变化率（%），上月为 0 时返回 `0.0` |

---

## 总体风险数（`GET /platform/dashboard/risk-overview`）

本月与上月各子服务检测到风险的任务数及环比。

**风险定义：**

| 服务 | 风险判断条件 | 数据来源 |
|------|-------------|---------|
| S1 (OAT) | `oat_scan_result.status = 'success'` 且 `total_issues > 0` | `oat_scan_result` 表 |
| S3 (sentry) | `s3_status = 'success'` 且 `s3_has_conflicts = true` | `platform_task` 表 |
| S2 / S4 / S5 | 预留，恒为 0，`integrated: false` | — |

### 响应（200）

```json
{
  "month": "2026-05",
  "total": {
    "current": 15,
    "last_month": 10,
    "change": 5,
    "change_rate": 50.0,
    "integrated": true
  },
  "by_service": {
    "s1": { "current": 8, "last_month": 6, "change": 2,  "change_rate": 33.33, "integrated": true  },
    "s2": { "current": 0, "last_month": 0, "change": 0,  "change_rate": 0.0,   "integrated": false },
    "s3": { "current": 7, "last_month": 4, "change": 3,  "change_rate": 75.0,  "integrated": true  },
    "s4": { "current": 0, "last_month": 0, "change": 0,  "change_rate": 0.0,   "integrated": false },
    "s5": { "current": 0, "last_month": 0, "change": 0,  "change_rate": 0.0,   "integrated": false }
  }
}
```

| 字段 | 说明 |
|------|------|
| `current` | 本月风险任务数 |
| `last_month` | 上月风险任务数 |
| `change` | 环比变化量 |
| `change_rate` | 环比变化率（%），上月为 0 时返回 `0.0` |
| `integrated` | 服务是否已接入，`false` 表示预留占位 |

> `s3_has_conflicts` 在 S3 扫描完成时由 `sentry_poll_task` 或 `POST /platform/tasks/{id}/s3/sync-status` 自动写入。

---

## 待处理扫描任务数（`GET /platform/dashboard/pending-risks`）

本月与上月各子服务处于 `pending` 或 `running` 状态（尚未得出结果）的任务数及环比，反映队列积压情况。

### 响应（200）

```json
{
  "month": "2026-05",
  "total": {
    "current": 5,
    "last_month": 3,
    "change": 2,
    "change_rate": 66.67,
    "integrated": true
  },
  "by_service": {
    "s1": { "current": 3, "last_month": 2, "change": 1, "change_rate": 50.0,  "integrated": true  },
    "s2": { "current": 0, "last_month": 0, "change": 0, "change_rate": 0.0,   "integrated": false },
    "s3": { "current": 2, "last_month": 1, "change": 1, "change_rate": 100.0, "integrated": true  },
    "s4": { "current": 0, "last_month": 0, "change": 0, "change_rate": 0.0,   "integrated": false },
    "s5": { "current": 0, "last_month": 0, "change": 0, "change_rate": 0.0,   "integrated": false }
  }
}
```

响应字段与 `risk-overview` 完全一致，含义对应为"待处理任务数"而非"风险数"。

---

## 最近 6 个月合规趋势（`GET /platform/dashboard/compliance-trend`）

最近 6 个自然月（含当前月份）每月扫描总量与风险占比，用于折线/柱状图展示。

### 响应（200）

```json
{
  "months": [
    {
      "month": "2025-12",
      "total_scans": 20,
      "risk_count": 8,
      "risk_rate": 40.0,
      "by_service": {
        "s1": { "scans": 10, "risks": 4 },
        "s2": { "scans": 0,  "risks": 0 },
        "s3": { "scans": 10, "risks": 4 },
        "s4": { "scans": 0,  "risks": 0 },
        "s5": { "scans": 0,  "risks": 0 }
      }
    },
    { "month": "2026-01", "total_scans": 18, "risk_count": 5,  "risk_rate": 27.78, "by_service": { "...": "..." } },
    { "month": "2026-02", "total_scans": 22, "risk_count": 9,  "risk_rate": 40.91, "by_service": { "...": "..." } },
    { "month": "2026-03", "total_scans": 30, "risk_count": 12, "risk_rate": 40.0,  "by_service": { "...": "..." } },
    { "month": "2026-04", "total_scans": 25, "risk_count": 10, "risk_rate": 40.0,  "by_service": { "...": "..." } },
    {
      "month": "2026-05",
      "total_scans": 15,
      "risk_count": 7,
      "risk_rate": 46.67,
      "by_service": {
        "s1": { "scans": 8, "risks": 4 },
        "s2": { "scans": 0, "risks": 0 },
        "s3": { "scans": 7, "risks": 3 },
        "s4": { "scans": 0, "risks": 0 },
        "s5": { "scans": 0, "risks": 0 }
      }
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `total_scans` | 当月所有服务合计完成扫描数（success + failed） |
| `risk_count` | 其中检测到风险的任务数 |
| `risk_rate` | 风险占比（%），当月无扫描时为 `null` |
| `by_service.s*.scans` | 该服务当月完成扫描数 |
| `by_service.s*.risks` | 该服务当月风险任务数 |

**数据来源：**

| 服务 | 完成扫描判断 | 风险判断 |
|------|------------|---------|
| S1 | `oat_scan_result` 有记录（任务参与了 S1 扫描） | `total_issues > 0` AND `status='success'` |
| S3 | `s3_status IN ('success','failed')` | `s3_status='success'` AND `s3_has_conflicts=true` |
| S2/S4/S5 | 预留，恒为 0 | 预留，恒为 0 |

**前端示例（ECharts）：**

```javascript
const res = await fetch('/platform/dashboard/compliance-trend')
const { months } = await res.json()
const xData      = months.map(m => m.month)
const totalSeries = months.map(m => m.total_scans)
const riskSeries  = months.map(m => m.risk_count)
const rateSeries  = months.map(m => m.risk_rate ?? 0)
```

---

## OAT 风险类型分布（饼图）（`GET /platform/dashboard/s1/risk-distribution`）

统计 OAT（S1）扫描结果中三类风险问题的数量与占比，供前端饼图展示。

### 请求参数（Query，均可选）

| 参数 | 类型 | 说明 |
|------|------|------|
| `month` | string | 按月筛选，格式 `YYYY-MM`（如 `2026-05`）。不传则统计全量历史数据。 |

### 风险类型说明

| `type` 值 | `label` | 说明 |
|-----------|---------|------|
| `invalid_file_type` | 文件类型不合规 | 归档/二进制文件被扫描到不符合规范 |
| `license_header_invalid` | License 头缺失/不合规 | 源文件缺少合规 License 声明头 |
| `copyright_header_invalid` | Copyright 头缺失/不合规 | 源文件缺少合规 Copyright 声明头 |

**统计口径：** 仅纳入 `status = 'success'` 的 OAT 扫描任务；传入 `month` 则按该自然月的 `created_at` 过滤。

### 响应（200）

```json
{
  "total": 1520,
  "scan_count": 38,
  "month": "2026-05",
  "items": [
    {
      "type": "invalid_file_type",
      "label": "文件类型不合规",
      "count": 210,
      "rate": 13.82
    },
    {
      "type": "license_header_invalid",
      "label": "License 头缺失/不合规",
      "count": 890,
      "rate": 58.55
    },
    {
      "type": "copyright_header_invalid",
      "label": "Copyright 头缺失/不合规",
      "count": 420,
      "rate": 27.63
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `total` | 三类风险问题总数（三项 `count` 之和，饼图分母） |
| `scan_count` | 统计范围内成功完成的 OAT 扫描任务数 |
| `month` | 若传入 `month` 参数则原样返回，否则为 `null`（表示全量统计） |
| `items[].type` | 风险类型标识符（可作为前端 key） |
| `items[].label` | 风险类型中文名（直接用于饼图图例） |
| `items[].count` | 该风险类型的累计问题数 |
| `items[].rate` | 占全部风险问题的百分比（保留两位小数，无数据时为 `0.0`） |

**前端示例（ECharts 饼图）：**

```javascript
const res = await fetch('/platform/dashboard/s1/risk-distribution?month=2026-05')
const { items, total, scan_count } = await res.json()
const pieData = items.map(i => ({ name: i.label, value: i.count }))
// option.series[0].data = pieData
```

---

## 多条件查询（`GET /platform/tasks/query`）

多条件组合过滤平台任务，支持分页，按创建时间倒序排列。

**Query 参数（均可选）：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 按 `task_id` 精确匹配 |
| `task_name` | string | 任务名称模糊匹配（包含即命中） |
| `task_status` | string | 整体状态：`active` / `completed` / `failed` / `deleted` |
| `ingest_id` | string | 按关联 `ingest_id` 查询 |
| `s1_status` | string | S1 服务状态：`pending` / `running` / `success` / `failed` / `skipped` |
| `s2_status` | string | S2 服务状态（同上） |
| `s3_status` | string | S3 服务状态（同上） |
| `s4_status` | string | S4 服务状态（同上） |
| `s5_status` | string | S5 服务状态（同上） |
| `created_after` | string | 创建时间下限（ISO 8601，含） |
| `created_before` | string | 创建时间上限（ISO 8601，含） |
| `include_deleted` | boolean | 是否包含已软删除记录，默认 `false` |
| `page` | integer | 页码，从 1 开始，默认 1 |
| `page_size` | integer | 每页条数，默认 20，最大 200 |

### 响应（200）

```json
{
  "total": 100,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 1,
      "task_id": "pt-a1b2c3d4...",
      "task_name": "my-project",
      "ingest_id": "42",
      "task_status": "completed",
      "s1_status": "success",
      "s2_status": "skipped",
      "s3_status": "success",
      "s4_status": "skipped",
      "s5_status": "skipped",
      "s3_analysis_id": "uuid-xxxx",
      "created_at": "2026-05-01T10:00:00Z",
      "updated_at": "2026-05-01T10:30:00Z",
      "deleted_at": null
    }
  ]
}
```

---

## 查询单条任务（`GET /platform/tasks/{task_id}`）

按 `task_id` 精确查询单条平台任务详情。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识，格式 `pt-<uuid4hex>` |

### 响应（200）

```json
{
  "id": 1,
  "task_id": "pt-a1b2c3d4...",
  "task_name": "my-project",
  "ingest_id": "42",
  "task_status": "active",
  "s1_status": "success",
  "s2_status": "skipped",
  "s3_status": "running",
  "s4_status": "skipped",
  "s5_status": "skipped",
  "s3_analysis_id": "uuid-xxxx",
  "created_at": "2026-05-01T10:00:00Z",
  "updated_at": "2026-05-01T10:05:00Z",
  "deleted_at": null
}
```

| 字段 | 说明 |
|------|------|
| `s3_analysis_id` | compliance-sentry 分析任务 ID，sentry 接受任务后写入，可用于直接查询 sentry 进度或调用 `sync-status` |

**任务整体状态（`task_status`）推导规则：**

| 值 | 条件 |
|----|------|
| `active` | 有服务仍处于 `pending` 或 `running` |
| `completed` | 所有选中服务均为 `success` |
| `failed` | 至少一个选中服务为 `failed` |
| `deleted` | 已软删除 |

**错误响应：**

- `404`：task_id 不存在

---

## 服务状态回调（`PATCH /platform/tasks/{task_id}/service-status`）

供平台内部扫描服务（S1~S5）在完成/失败时回调，更新自身状态并自动推导整体状态。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识 |

**Content-Type**：`application/json`

**请求体：**

```json
{
  "service": "S1",
  "status": "success",
  "message": "可选附加说明"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `service` | string | 是 | `S1` / `S2` / `S3` / `S4` / `S5` |
| `status` | string | 是 | `running` / `success` / `failed` / `skipped` |
| `message` | string | 否 | 附加说明，最多 500 字符 |

### 响应（200）

```json
{
  "task_id": "pt-a1b2c3d4...",
  "service": "S1",
  "service_status": "success",
  "task_status": "completed",
  "updated_at": "2026-05-01T10:30:00Z"
}
```

**错误响应：**

- `404`：task_id 不存在
- `409`：任务已软删除，无法更新

---

## 实时同步 S3 状态（`POST /platform/tasks/{task_id}/s3/sync-status`）

主动从 compliance-sentry 查询最新扫描状态，并同步写入平台任务表。

**适用场景**：Celery worker 未启动或轮询任务失败，导致 `s3_status` 长期停留在 `running`；或前端需要立即刷新状态。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识 |

**行为逻辑：**

1. 读取 `platform_task.s3_analysis_id`；
2. 若 `s3_status` 已为终态（`success`/`failed`/`skipped`），直接返回当前值（`synced: false`）；
3. 调用 sentry `GET /analysis/{id}/status`，映射状态；
4. 若 sentry 已 `completed`，额外调用 `GET /analysis/{id}/conflicts` 获取冲突数，一并写入 `s3_has_conflicts`、`s3_conflict_count`；
5. 返回同步结果。

**sentry 状态映射：**

| sentry `current_status` | 写入 `s3_status` |
|------------------------|----------------|
| `completed` | `success` |
| `failed` | `failed` |
| `terminated` | `failed` |
| `pending` / `running` | 不变（保持 `running`） |

### 响应（200）— 已同步

```json
{
  "task_id": "pt-a1b2c3d4...",
  "s3_analysis_id": "uuid-xxxx",
  "sentry_status": "completed",
  "sentry_progress": 100,
  "s3_status": "success",
  "s3_has_conflicts": true,
  "s3_conflict_count": 3,
  "task_status": "completed",
  "synced": true,
  "updated_at": "2026-05-13T09:00:00Z"
}
```

### 响应（200）— 已是终态，无需同步

```json
{
  "task_id": "pt-a1b2c3d4...",
  "s3_status": "success",
  "task_status": "completed",
  "s3_analysis_id": "uuid-xxxx",
  "synced": false,
  "reason": "s3_status already in terminal state: success"
}
```

### 响应（200）— `s3_analysis_id` 尚未写入

```json
{
  "task_id": "pt-a1b2c3d4...",
  "s3_status": "pending",
  "task_status": "active",
  "s3_analysis_id": null,
  "synced": false,
  "reason": "s3_analysis_id not set yet (sentry job may not have been accepted)"
}
```

**错误响应：**

- `404`：task_id 不存在
- `503`：sentry 认证失败或 `COMPLIANCE_SENTRY_BASE_URL` 未配置
- `502`：连接 sentry 失败或 sentry 返回错误

**前端轮询示例：**

```javascript
const pollS3 = async (taskId) => {
  const res = await fetch(`/platform/tasks/${taskId}/s3/sync-status`, { method: 'POST' })
  const data = await res.json()
  const done = ['success', 'failed', 'skipped'].includes(data.s3_status)
  return { done, data }
}
const timer = setInterval(async () => {
  const { done, data } = await pollS3(platformTaskId)
  if (done) { clearInterval(timer); console.log('S3 完成', data) }
}, 10000)
```

---

## 列出规则配置（`GET /platform/oat-rules`）

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `is_active` | boolean | 按启用状态过滤；不传则返回全部 |

### 响应（200）

```json
{
  "total": 2,
  "items": [
    {
      "id": 1,
      "name": "华为默认规则",
      "description": "适用于 CANN 系列仓库",
      "xml_content": "<?xml version=\"1.0\"...>",
      "is_active": true,
      "created_at": "2026-04-01T08:00:00Z",
      "updated_at": "2026-04-01T08:00:00Z"
    }
  ]
}
```

> `xml_content` 为 `null` 时表示该配置不叠加自定义规则，直接使用 oat_python 内置默认规则。

---

## 创建规则配置（`POST /platform/oat-rules`）

**Content-Type**：`application/json`

**请求体：**

```json
{
  "name": "华为 CANN 规则",
  "description": "适用于 CANN 系列仓库",
  "xml_content": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<configuration>...</configuration>",
  "is_active": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 规则配置名称，全局唯一，最多 255 字符 |
| `description` | string | 否 | 描述，最多 500 字符 |
| `xml_content` | string | 否 | OAT XML 规则内容；**留空/null 表示使用内置默认规则** |
| `is_active` | boolean | 否 | 是否启用，默认 `true` |

### 响应（201）

```json
{
  "id": 2,
  "name": "华为 CANN 规则",
  "description": "适用于 CANN 系列仓库",
  "xml_content": "...",
  "is_active": true,
  "created_at": "2026-05-11T10:00:00Z",
  "updated_at": "2026-05-11T10:00:00Z"
}
```

**错误响应：**

- `409`：`name` 已存在

---

## 查看内置默认 XML（`GET /platform/oat-rules/builtin-xml`）

返回 oat_python 随包携带的内置规则 XML，**只读**，供参考编写自定义规则。

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `variant` | string | `default`（默认，OAT-Default.xml）或 `common`（OAT-Common.xml） |

### 响应（200）

```json
{
  "filename": "OAT-Default.xml",
  "xml_content": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>..."
}
```

**错误响应：**

- `404`：内置 XML 文件不存在（工具未正确安装）

---

## 获取单条规则（`GET /platform/oat-rules/{rule_id}`）

**路径参数：**

| 参数 | 说明 |
|------|------|
| `rule_id` | 规则配置 ID（整数） |

### 响应（200）

同创建规则配置的响应结构。

**错误响应：**

- `404`：规则不存在

---

## 更新规则配置（`PUT /platform/oat-rules/{rule_id}`）

所有字段均可选，仅传入需要修改的字段。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `rule_id` | 规则配置 ID（整数） |

**Content-Type**：`application/json`

**请求体（所有字段可选）：**

```json
{
  "name": "新规则名称",
  "description": "更新后的描述",
  "xml_content": "<?xml version=\"1.0\"...>",
  "is_active": false
}
```

### 响应（200）

同创建规则配置的响应结构，返回更新后的完整配置。

**错误响应：**

- `404`：规则不存在
- `409`：新 `name` 与其他规则重名

---

## 删除规则配置（`DELETE /platform/oat-rules/{rule_id}`）

物理删除（不可恢复）。已关联该规则的历史扫描记录 `rule_config_id` 字段保留原值。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `rule_id` | 规则配置 ID（整数） |

### 响应（204）

无响应体。

**错误响应：**

- `404`：规则不存在

---

## 扫描任务列表查询（`GET /platform/oat-scan-results`）

多条件筛选 OAT 扫描任务列表，支持分页与排序。返回结果**不含**三类 issue 详情数组和原始报告文本（减少传输量），如需详情请调用单条接口。

### 请求参数（Query，均可选）

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | integer | 页码，从 1 开始，默认 `1` |
| `page_size` | integer | 每页记录数，默认 `20`，最大 `100` |
| `platform_task_id` | string | 按平台任务 ID **模糊匹配**（支持部分输入） |
| `status` | string | 扫描状态：`running` / `success` / `failed` / `cancelled` |
| `rule_config_id` | integer | 按规则配置 ID 精确筛选；传 `0` 表示只查使用**内置默认规则**（`rule_config_id IS NULL`）的记录 |
| `exit_code` | integer | 按 oat_python 退出码精确筛选（`0`=无问题，`1`=有 issue） |
| `min_total_issues` | integer | issue 总数下限（含） |
| `max_total_issues` | integer | issue 总数上限（含） |
| `has_issues` | boolean | `true`=只返回有 issue 的记录（`total_issues > 0`）；`false`=只返回无 issue 的记录 |
| `start_date` | string | `created_at` 起始时间（ISO 8601 字符串 或 13 位毫秒时间戳） |
| `end_date` | string | `created_at` 截止时间（格式同上） |
| `sort_by` | string | 排序字段，默认 `created_at`；可选：`updated_at` / `total_issues` / `invalid_file_type_count` / `license_header_invalid_count` / `copyright_header_invalid_count` |
| `sort_order` | string | 排序方向：`desc`（默认）/ `asc` |

### 响应（200）

```json
{
  "total": 86,
  "page": 1,
  "page_size": 20,
  "total_pages": 5,
  "items": [
    {
      "id": 42,
      "platform_task_id": "pt-a1b2c3d4...",
      "rule_config_id": null,
      "celery_task_id": "celery-uuid-xxxx",
      "status": "success",
      "exit_code": 1,
      "total_issues": 5,
      "invalid_file_type_count": 0,
      "license_header_invalid_count": 3,
      "copyright_header_invalid_count": 2,
      "error_message": null,
      "created_at": "2026-05-19T10:00:00Z",
      "updated_at": "2026-05-19T10:05:00Z"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `total` | 符合筛选条件的总记录数 |
| `total_pages` | 总页数 |
| `items` | 当前页记录列表（摘要，不含 issue 详情数组） |

---

## 查询单条扫描结果（`GET /platform/oat-scan-results/{task_id}`）

按 `platform_task_id` 查询最新一条 OAT（S1）扫描结果，返回完整内容，包含三类 issue 结构化详情和原始报告文本。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识（`pt-xxxx` 格式） |

### 响应（200）

```json
{
  "id": 10,
  "platform_task_id": "pt-a1b2c3d4...",
  "rule_config_id": 2,
  "celery_task_id": "celery-uuid-xxxx",
  "status": "success",
  "exit_code": 1,
  "total_issues": 5,
  "invalid_file_type_count": 0,
  "license_header_invalid_count": 3,
  "copyright_header_invalid_count": 2,
  "invalid_file_type_issues": [],
  "license_header_invalid_issues": [
    { "file": "src/utils.c",      "content": "NoLicenseHeader", "project": "my-repo" },
    { "file": "src/network/tcp.c","content": "GPL-2.0-only",    "project": "my-repo" },
    { "file": "src/core/mem.c",   "content": "InvalidLicense",  "project": "my-repo" }
  ],
  "copyright_header_invalid_issues": [
    { "file": "src/utils.c",   "content": "NULL",               "project": "my-repo" },
    { "file": "src/core/mem.c","content": "Copyright 2020 Acme","project": "my-repo" }
  ],
  "report_text": "...(PlainReport_*.txt 完整内容)...",
  "error_message": null,
  "created_at": "2026-05-11T10:00:00Z",
  "updated_at": "2026-05-11T10:05:00Z"
}
```

**顶层字段说明：**

| 字段 | 说明 |
|------|------|
| `status` | `running` / `success` / `failed` / `cancelled` |
| `exit_code` | oat_python 退出码：`0`=无问题，`1`=有 issue，`-1`=超时/崩溃，`null`=未完成 |
| `total_issues` | 三类 issue 之和（`0` 表示完全合规） |
| `rule_config_id` | 使用的规则配置 ID，`null` 表示内置默认规则 |
| `celery_task_id` | 异步模式下的 Celery task ID |
| `report_text` | `PlainReport_*.txt` 完整原始内容（MySQL MEDIUMTEXT，最大 16 MB） |
| `error_message` | 扫描执行异常时的错误说明 |

**三类 issue 详情数组：**

> `null` 表示该记录为旧数据（功能上线前产生），尚未解析；`[]` 表示该类无问题。

| 字段 | 说明 |
|------|------|
| `invalid_file_type_issues` | 文件类型不合规问题列表 |
| `license_header_invalid_issues` | License 头缺失/不合规问题列表 |
| `copyright_header_invalid_issues` | Copyright 头缺失/不合规问题列表 |

**issue 条目字段：**

| 字段 | 说明 |
|------|------|
| `file` | 问题文件路径（相对仓库根） |
| `content` | issue 具体内容（含义随类型不同，见下表） |
| `project` | oat 扫描时使用的项目名称 |

**`content` 含义说明：**

| 类型 | `content` 示例 | 含义 |
|------|---------------|------|
| `invalid_file_type_issues` | `unknown` / `binary` | 识别到的文件类型 |
| `license_header_invalid_issues` | `NoLicenseHeader` | 文件缺少 License 声明头 |
| | `InvalidLicense` | 存在许可证声明但格式/内容不合规 |
| | `GPL-2.0-only` | 文件使用了该许可证（可能未通过兼容性检查） |
| `copyright_header_invalid_issues` | `NULL` | 文件缺少 Copyright 声明头 |
| | `Copyright 2024 Xxx` | 版权声明内容（多个所有者以 ` \|` 分隔） |

**错误响应：**

- `404`：该任务无 OAT 扫描记录

**前端轮询示例（异步模式）：**

```javascript
const pollOAT = async (taskId) => {
  const task = await fetch(`/platform/tasks/${taskId}`).then(r => r.json())
  if (['success', 'failed'].includes(task.s1_status)) {
    return await fetch(`/platform/oat-scan-results/${taskId}`).then(r => r.json())
  }
  return null
}
const timer = setInterval(async () => {
  const result = await pollOAT(platformTaskId)
  if (result) { clearInterval(timer); console.log('OAT 扫描完成', result) }
}, 5000)
```

---

## 取消 S1 扫描（`DELETE /platform/tasks/{task_id}/s1`）

取消正在进行或等待中的 S1（OAT）扫描。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识 |

**行为：**

1. 查找该任务最新一条 `status='running'` 的 `oat_scan_result` 记录；
2. 若存在 `celery_task_id`，向 Celery worker 发送 `SIGTERM` 终止进程；
3. 将 `oat_scan_result.status` 更新为 `cancelled`；
4. 将 `platform_task.s1_status` 更新为 `failed`，重新推导 `task_status`。

### 响应（200）

```json
{
  "status": "cancelled",
  "task_id": "pt-a1b2c3d4...",
  "s1_status": "failed",
  "celery_task_id": "celery-uuid-xxxx",
  "celery_revoked": true
}
```

| 字段 | 说明 |
|------|------|
| `celery_task_id` | Celery 任务 ID；同步扫描时为 `null` |
| `celery_revoked` | `true`=已成功发送终止信号；`false`=发送失败（进程可能已结束） |

**错误响应：**

- `404`：platform_task 不存在
- `409`：S1 扫描已处于终态（`success`/`failed`），不可取消

---

## 数据模型参考

### `platform_task` 表关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 唯一标识，格式 `pt-<uuid4hex>` |
| `task_name` | string | 用户提交的任务名称 |
| `ingest_id` | int | 关联 `file_ingest_result.id` |
| `task_status` | string | `active` / `completed` / `failed` / `deleted` |
| `s1_status` | string | S1 OAT 扫描状态 |
| `s3_status` | string | S3 compliance-sentry 扫描状态 |
| `s3_analysis_id` | string \| null | sentry 分析任务 ID |
| `s3_has_conflicts` | bool \| null | sentry 是否检测到冲突，扫描完成后写入 |
| `s3_conflict_count` | int | sentry 冲突数量，扫描完成后写入 |

### 服务状态值（`s1_status` / `s3_status` 等）

| 值 | 说明 |
|----|------|
| `pending` | 已创建，等待扫描开始 |
| `running` | 扫描正在进行中 |
| `success` | 扫描完成（可能有 issue，见各服务结果表） |
| `failed` | 扫描失败或被取消 |
| `skipped` | 当前任务未选用该服务 |

### `oat_scan_result` 表关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 自增主键 |
| `platform_task_id` | string | 关联 `platform_task.task_id` |
| `rule_config_id` | integer \| null | 使用的规则配置 ID；`null` = 内置默认规则 |
| `status` | string | `running` / `success` / `failed` / `cancelled` |
| `exit_code` | integer \| null | oat_python 进程退出码（`0`=无问题，`1`=有 issue，`-1`=超时，`null`=未完成） |
| `total_issues` | integer | 三类 issue 之和 |
| `invalid_file_type_count` | integer | Invalid File Type 问题数 |
| `license_header_invalid_count` | integer | License Header Invalid 问题数 |
| `copyright_header_invalid_count` | integer | Copyright Header Invalid 问题数 |
| `invalid_file_type_issues` | JSON \| null | Invalid File Type 问题详情列表；`null` 表示旧数据未解析 |
| `license_header_invalid_issues` | JSON \| null | License Header Invalid 问题详情列表 |
| `copyright_header_invalid_issues` | JSON \| null | Copyright Header Invalid 问题详情列表 |
| `report_text` | text \| null | PlainReport_*.txt 完整内容（MySQL MEDIUMTEXT，最大 16 MB） |
| `celery_task_id` | string \| null | 异步模式下的 Celery task ID |
| `error_message` | string \| null | 执行异常时的错误信息 |
| `created_at` | datetime | 记录创建时间（UTC） |
| `updated_at` | datetime | 最后更新时间（UTC） |

> **数据库迁移说明（已存在的表）：**  
> 首次部署上线时需手动执行以下 SQL：
> ```sql
> ALTER TABLE oat_scan_result
>   MODIFY COLUMN report_text MEDIUMTEXT
>     COMMENT 'PlainReport_*.txt 完整内容（MEDIUMTEXT 最大 16MB）',
>   ADD COLUMN invalid_file_type_issues        JSON NULL COMMENT 'Invalid File Type 问题详情',
>   ADD COLUMN license_header_invalid_issues   JSON NULL COMMENT 'License Header Invalid 问题详情',
>   ADD COLUMN copyright_header_invalid_issues JSON NULL COMMENT 'Copyright Header Invalid 问题详情';
> ```

### `oat_scan_result.status` 值

| 值 | 说明 |
|----|------|
| `running` | OAT 进程执行中 |
| `success` | 扫描完成（可能有 issue，见 `total_issues`） |
| `failed` | 执行异常或超时 |
| `cancelled` | 被 `DELETE /platform/tasks/{id}/s1` 主动取消 |

### OAT 规则叠加说明

oat_python 按如下优先级（低→高）合并规则：

```
内置 OAT-Default.xml（始终加载）
          ↓  通过 -oatconfig 叠加
自定义规则 XML（oat_rule_config.xml_content）
```

| XML 节点 | 合并方式 |
|---------|---------|
| `filefilter` | 追加到同名过滤器 |
| `policy.copyright` | 替换默认版权规则 |
| `policy.filetype` | 替换默认文件类型规则 |
| `policy.license` | 前置追加（OR 语义，两者同时生效） |
| `licensematcher` | 追加到全局列表 |
| `licensecompatibilitylist` | 追加到全局兼容性表 |
