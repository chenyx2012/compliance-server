# 平台自有接口说明

本文档说明合规平台网关（compliance_sever）的**平台自有接口**，包括：

- [首页看板](#首页看板get-platformdashboard)
- [平台任务管理](#平台任务管理)
  - [多条件查询](#多条件查询get-platformtasksquery)
  - [查询单条任务](#查询单条任务get-platformtaskstask_id)
  - [服务状态回调](#服务状态回调patch-platformtaskstask_idservice-status)
- [OAT 规则配置管理](#oat-规则配置管理)
  - [列出规则配置](#列出规则配置get-platformoat-rules)
  - [创建规则配置](#创建规则配置post-platformoat-rules)
  - [查看内置默认 XML](#查看内置默认-xmlget-platformoat-rulesbuiltin-xml)
  - [获取单条规则](#获取单条规则get-platformoat-rulesrule_id)
  - [更新规则配置](#更新规则配置put-platformoat-rulesrule_id)
  - [删除规则配置](#删除规则配置delete-platformoat-rulesrule_id)
- [OAT 扫描结果](#oat-扫描结果)
  - [查询扫描结果](#查询扫描结果get-platformoat-scan-resultstask_id)
  - [取消 S1 扫描](#取消-s1-扫描delete-platformtaskstask_ids1)

---

## 首页看板（`GET /platform/dashboard`）

获取首页看板核心指标：监控项目总数及环比涨跌。

**统计口径**：`platform_task` 表中 `task_status != 'deleted'` 的记录，按 `created_at` 所在自然月计数。

### 请求

```http
GET /platform/dashboard
```

无请求参数。

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

| 字段 | 类型 | 说明 |
|------|------|------|
| `month` | string | 当前统计月份，格式 `YYYY-MM` |
| `monitor_projects.current` | integer | 本月新增监控项目数 |
| `monitor_projects.last_month` | integer | 上月监控项目数 |
| `monitor_projects.change` | integer | 环比变化量（本月 - 上月），正为增，负为减 |
| `monitor_projects.change_rate` | number \| null | 环比变化率（%），保留 2 位小数；**上月为 0 时返回 `null`** |

### 前端调用示例

```javascript
const res = await fetch('/platform/dashboard')
const { month, monitor_projects } = await res.json()

// 渲染环比
const trend = monitor_projects.change_rate !== null
  ? `${monitor_projects.change_rate > 0 ? '+' : ''}${monitor_projects.change_rate}%`
  : '—'
```

---

## 平台任务管理

> 所有接口数据来自平台本地 MySQL，与 compliance-sentry 无关，无需鉴权。

---

### 多条件查询（`GET /platform/tasks/query`）

支持多个可选条件组合过滤，支持分页，按创建时间倒序排列。

**Query 参数（均可选）：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 按 `task_id` 精确匹配 |
| `task_name` | string | 任务名称模糊匹配（包含即命中） |
| `task_status` | string | 整体状态过滤：`active` / `completed` / `failed` / `deleted` |
| `ingest_id` | integer | 按关联 `ingest_id` 查询 |
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

**响应（200）：**

```json
{
  "total": 100,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 1,
      "task_id": "pt-a1b2c3d4e5f6...",
      "task_name": "my-project",
      "ingest_id": 42,
      "task_status": "completed",
      "s1_status": "success",
      "s2_status": "skipped",
      "s3_status": "success",
      "s4_status": "skipped",
      "s5_status": "skipped",
      "created_at": "2026-05-01T10:00:00Z",
      "updated_at": "2026-05-01T10:30:00Z",
      "deleted_at": null
    }
  ]
}
```

**前端调用示例：**

```javascript
// 查询正在运行中的 S1 扫描任务，第 1 页
const params = new URLSearchParams({
  s1_status: 'running',
  page: 1,
  page_size: 20
})
const res = await fetch(`/platform/tasks/query?${params}`)
const { total, items } = await res.json()
```

---

### 查询单条任务（`GET /platform/tasks/{task_id}`）

按 `task_id` 精确查询单条平台任务详情。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识，格式 `pt-<uuid4hex>` |

**响应（200）：**

```json
{
  "id": 1,
  "task_id": "pt-a1b2c3d4e5f6...",
  "task_name": "my-project",
  "ingest_id": 42,
  "task_status": "active",
  "s1_status": "running",
  "s2_status": "skipped",
  "s3_status": "pending",
  "s4_status": "skipped",
  "s5_status": "skipped",
  "created_at": "2026-05-01T10:00:00Z",
  "updated_at": "2026-05-01T10:05:00Z",
  "deleted_at": null
}
```

**任务整体状态（`task_status`）说明：**

| 值 | 触发条件 |
|----|---------|
| `active` | 有服务仍处于 `pending` 或 `running` |
| `completed` | 所有选中服务均为 `success` |
| `failed` | 至少一个选中服务为 `failed` |
| `deleted` | 已软删除 |

**错误响应：**

- `404`：task_id 不存在

---

### 服务状态回调（`PATCH /platform/tasks/{task_id}/service-status`）

> 供平台内部各扫描服务（S1~S5）在完成/失败时回调，更新自身状态并自动推导整体状态。前端通常无需调用此接口。

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
  "message": "可选的附加说明（如失败原因）"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `service` | string | 是 | `S1` / `S2` / `S3` / `S4` / `S5` |
| `status` | string | 是 | `running` / `success` / `failed` / `skipped` |
| `message` | string | 否 | 附加说明，最多 500 字符 |

**响应（200）：**

```json
{
  "task_id": "pt-a1b2c3d4e5f6...",
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

## OAT 规则配置管理

> OAT（Open source Audit Tool）是平台内置的开源合规扫描工具（S1 服务）。  
> 前端通过此组接口管理规则配置，在提交 `POST /platform/tasks` 时通过 `s1_rule_config_id` 指定使用哪套规则。  
> 不指定则使用 oat_python 内置默认规则（`OAT-Default.xml`）。

---

### 列出规则配置（`GET /platform/oat-rules`）

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `is_active` | boolean | 按启用状态过滤；不传则返回全部 |

**响应（200）：**

```json
{
  "total": 3,
  "items": [
    {
      "id": 1,
      "name": "华为默认规则",
      "description": "适用于 CANN 系列仓库的合规规则",
      "xml_content": "<?xml version=\"1.0\"...>",
      "is_active": true,
      "created_at": "2026-04-01T08:00:00Z",
      "updated_at": "2026-04-01T08:00:00Z"
    }
  ]
}
```

> `xml_content` 为 `null` 时表示该配置不叠加任何自定义规则，直接使用 oat_python 内置默认规则。

---

### 创建规则配置（`POST /platform/oat-rules`）

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
| `xml_content` | string | 否 | OAT XML 规则内容（完整 XML 字符串）；**留空/null 表示使用内置默认规则** |
| `is_active` | boolean | 否 | 是否启用，默认 `true` |

**XML 规则格式参考：**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <oatconfig>
    <policylist>
      <policy name="projectPolicy" desc="自定义策略">
        <!-- 允许 Apache-2.0 许可证 -->
        <policyitem type="license" name="Apache-2.0" path=".*"
                    rule="may" group="defaultGroup"
                    filefilter="defaultPolicyFilter" desc=""/>
        <!-- 版权持有者 -->
        <policyitem type="copyright" name="My Company Co., Ltd." path=".*"
                    rule="may" group="defaultGroup"
                    filefilter="copyrightPolicyFilter" desc=""/>
      </policy>
    </policylist>
    <filefilterlist>
      <!-- 扩展默认过滤器，排除不需要扫描的路径 -->
      <filefilter name="defaultFilter" desc="">
        <filteritem type="filepath" name="projectroot/vendor/.*" desc="第三方依赖目录"/>
      </filefilter>
    </filefilterlist>
  </oatconfig>
</configuration>
```

> XML 会与 oat_python 内置 `OAT-Default.xml` **叠加合并**（不替换），合并规则详见 [OAT 规则叠加说明](#oat-规则叠加说明)。

**响应（201）：**

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

### 查看内置默认 XML（`GET /platform/oat-rules/builtin-xml`）

查看 oat_python 随包携带的内置规则 XML，**只读**，供参考编写自定义规则。

**Query 参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `variant` | string | `default`（默认，OAT-Default.xml）或 `common`（OAT-Common.xml，CANN 通用模板） |

**响应（200）：**

```json
{
  "filename": "OAT-Default.xml",
  "xml_content": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>..."
}
```

---

### 获取单条规则（`GET /platform/oat-rules/{rule_id}`）

**路径参数：**

| 参数 | 说明 |
|------|------|
| `rule_id` | 规则配置 ID（整数） |

**响应（200）：** 同创建响应结构。

**错误响应：**

- `404`：规则不存在

---

### 更新规则配置（`PUT /platform/oat-rules/{rule_id}`）

所有字段均为可选，仅传入需要修改的字段。

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

**响应（200）：** 同创建响应结构，返回更新后的完整配置。

**错误响应：**

- `404`：规则不存在
- `409`：新 `name` 与其他规则重名

---

### 删除规则配置（`DELETE /platform/oat-rules/{rule_id}`）

物理删除规则配置（不可恢复）。已通过该规则扫描的历史记录 `oat_scan_result.rule_config_id` 保留原值，不受影响。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `rule_id` | 规则配置 ID（整数） |

**响应（204）：** 无响应体。

**错误响应：**

- `404`：规则不存在

---

## OAT 扫描结果

---

### 查询扫描结果（`GET /platform/oat-scan-results/{task_id}`）

按 `platform_task_id` 查询最新一条 OAT（S1）扫描结果。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识（`pt-xxxx` 格式） |

**响应（200）：**

```json
{
  "id": 10,
  "platform_task_id": "pt-a1b2c3d4e5f6...",
  "rule_config_id": 2,
  "celery_task_id": "celery-uuid-xxxx",
  "status": "success",
  "exit_code": 1,
  "total_issues": 3,
  "invalid_file_type_count": 0,
  "license_header_invalid_count": 2,
  "copyright_header_invalid_count": 1,
  "report_text": "Invalid File Type Total Count: 0\n\nLicense Header Invalid Total Count: 2\n...",
  "error_message": null,
  "created_at": "2026-05-11T10:00:00Z",
  "updated_at": "2026-05-11T10:05:00Z"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | `running` / `success` / `failed` / `cancelled` |
| `exit_code` | integer \| null | oat_python 进程退出码：`0`=无问题，`1`=有 issue，`-1`=超时，`null`=尚未完成 |
| `total_issues` | integer | 三类 issue 之和（`0` 表示完全合规） |
| `report_text` | string \| null | `PlainReport_*.txt` 内容（截断至 64 KB），含各类问题明细 |
| `rule_config_id` | integer \| null | 使用的规则配置 ID，`null` 表示使用内置默认规则 |
| `celery_task_id` | string \| null | 异步模式下对应的 Celery task ID |

**错误响应：**

- `404`：该任务无 OAT 扫描记录

**前端轮询示例（异步模式）：**

```javascript
const pollS1 = async (platformTaskId) => {
  const task = await fetch(`/platform/tasks/${platformTaskId}`).then(r => r.json())
  if (['success', 'failed'].includes(task.s1_status)) {
    // 扫描结束，获取详情
    const result = await fetch(`/platform/oat-scan-results/${platformTaskId}`).then(r => r.json())
    return result
  }
  return null // 仍在 running/pending，继续等待
}

// 每 5 秒轮询一次
const timer = setInterval(async () => {
  const result = await pollS1(platformTaskId)
  if (result) {
    clearInterval(timer)
    console.log('OAT 扫描完成', result)
  }
}, 5000)
```

---

### 取消 S1 扫描（`DELETE /platform/tasks/{task_id}/s1`）

取消正在进行或等待中的 S1（OAT）扫描。

- 若为异步模式（Celery），向 worker 发送 `SIGTERM` 终止进程；
- 将 `platform_task.s1_status` 标记为 `failed`；
- 将 `oat_scan_result.status` 标记为 `cancelled`。

**路径参数：**

| 参数 | 说明 |
|------|------|
| `task_id` | 平台任务唯一标识 |

**响应（200）：**

```json
{
  "status": "cancelled",
  "task_id": "pt-a1b2c3d4e5f6...",
  "s1_status": "failed",
  "celery_task_id": "celery-uuid-xxxx",
  "celery_revoked": true
}
```

| 字段 | 说明 |
|------|------|
| `celery_task_id` | Celery 任务 ID（若为同步扫描则为 `null`） |
| `celery_revoked` | `true`=已成功向 Celery worker 发送终止信号；`false`=发送失败（进程可能已结束） |

**错误响应：**

- `404`：platform_task 不存在
- `409`：S1 扫描已处于终态（`success` 或 `failed`），无需取消

---

## OAT 规则叠加说明

oat_python 按如下优先级（低→高）合并所有规则来源：

```
内置 OAT-Default.xml（oat_python 随包自带，始终加载）
          ↓  通过 -oatconfig 叠加（+）
自定义规则 XML（oat_rule_config.xml_content，写入临时文件）
```

**各节点合并语义（由 oat_python `loader.py` 定义，本平台不修改）：**

| XML 节点 | 合并方式 |
|----------|---------|
| `filefilter` | **追加**：自定义过滤项附加到同名过滤器尾部 |
| `policy.copyright` | **替换**：自定义版权规则替换默认版权规则 |
| `policy.filetype` | **替换**：自定义文件类型规则替换默认规则 |
| `policy.license` | **前置追加**：自定义许可证规则插入到默认规则前（OR 语义，两者同时生效） |
| `licensematcher` | **追加**：自定义许可证全文匹配文本追加到全局列表 |
| `licensecompatibilitylist` | **追加**：自定义兼容性条目追加到全局兼容性表 |

> 若要**完全覆盖**某类默认规则，需在 XML 中使用 `<policy type="filetype" ...>` 等替换型节点（`copyright` / `filetype`）。`license` 类型始终是叠加（OR），无法通过配置完全覆盖默认许可证规则。

---

## 数据模型参考

### `platform_task` 服务状态值

| 值 | 说明 |
|----|------|
| `pending` | 已创建，等待扫描开始 |
| `running` | 扫描正在进行中 |
| `success` | 扫描完成，无报错 |
| `failed` | 扫描失败或被取消 |
| `skipped` | 当前任务未选用该服务 |

### `oat_scan_result.status` 值

| 值 | 说明 |
|----|------|
| `running` | OAT 进程执行中 |
| `success` | 扫描完成（可能有 issue，见 `total_issues`） |
| `failed` | 执行异常或超时 |
| `cancelled` | 被 `DELETE /platform/tasks/{id}/s1` 主动取消 |
