# 服务器内置 AI Provider（Docker/Web 环境变量直用）

日期：2026-08-12
状态：已批准（用户批准全部设计段落，并授权子代理直接实施，不再提问）

## 问题

Docker/Web 部署时，若服务器配置了 `AI_ENDPOINT` / `AI_API_KEY` / `AI_MODEL` 环境变量，前端只要不在配置页填写任何字段，`configStore.configured`（`frontend/src/stores/config.ts:21`）即为 false，导致 GenerateView / PracticeView / SearchPanel 出现"去配置"提示并禁用按钮，无法直接使用服务器内置算力。

后端（Axum）已支持 env 回退：所有 AI 端点收到空 `endpoint` / `api_key` / `model` 时自动使用环境变量（`packages/server/src/routes.rs:123-125`）。问题纯在前端门槛。

## 目标

- 服务器配了环境变量时，前端**自动探测并自动选中**内置 AI provider，打开即用、零点击。
- 未配环境变量时，行为与现在完全一致（显示"去配置"）。
- 用户保存过自定义配置时，自定义配置优先。
- 仅影响 web/Docker（httpApi）路径；Tauri（无 env 回退）、CF Worker（已有 cf-free provider）零改动。

## 方案

镜像 CF 版 `cf-free` provider 模式，为 http 平台新增 `'server'` provider，并在启动时自动探测。

### 1. 后端：新端点 `GET /api/config/server`（packages/server/src/routes.rs）

- 路由挂到既有 Axum router（`/api/config/...` 同组）。
- 响应（serde Serialize）：

```rust
#[derive(Serialize)]
pub struct ServerConfigInfo {
    pub has_env_ai: bool,
    pub endpoint: String,
    pub model: String,
}
```

- `has_env_ai = !AI_ENDPOINT.is_empty() && !AI_API_KEY.is_empty()`（复用 `ai_endpoint()` / `ai_api_key()` 读取）。
- `endpoint` 返回 `ai_endpoint()` 值，`model` 返回 `ai_model()` 值，供前端展示与预填。
- **绝不返回 api_key**。
- 无状态，不需要 `AppState`。

### 2. 前端 API 层：httpApi.getServerInfo()

`frontend/src/api/http.ts` 新增：

```ts
async getServerInfo(): Promise<ServerConfigInfo | null> {
  try {
    const res = await fetch(`${BASE_URL}/api/config/server`)
    if (res.ok) return await res.json()
  } catch {}
  return null
}
```

失败静默返回 null（服务器不可达时界面退回现状）。

### 3. 状态层：config store（frontend/src/stores/config.ts）

- `AIProvider = 'cf-free' | 'custom' | 'server'`（扩展现有类型）。
- 新增 `serverInfo = ref<ServerConfigInfo | null>(null)`。
- `configured`：http 平台且 `aiProvider === 'server'` 时恒为 `true`（model 可空，空即服务器 `AI_MODEL` 默认）。
- `loadSaved()`（http 平台分支，即 `!isCloudflare() && !isTauri()`）执行顺序：
  1. 并行执行 `api.loadConfig()` 与 `api.getServerInfo()`（后者失败返回 null）；
  2. 决策优先级：
     a. `localStorage['exameow_ai_provider'] === 'server'` → 用内置 AI（预填 `serverInfo.model`）；
     b. 服务器已存自定义配置非空 → custom；
     c. `serverInfo?.has_env_ai` → 自动选 `'server'` 并预填 model。
- `fetchModels()` 新增 `'server'` 分支：以空 `endpoint`/`api_key` 调 `api.getModels(...)`，走服务器 env 回退（`/api/models` 空参数已支持）。
- `save()` 在 `'server'` 时只写 `localStorage['exameow_ai_provider']`，不调用 `api.saveConfig`（避免空配置覆盖服务器已存的自定义配置）。
- `getConfig()` 在 `'server'` 时返回 `{ endpoint: '', api_key: '', model: 用户所选或空 }` —— 各 Axum handler 自动回退 env。

### 4. ConfigView 改动（frontend/src/views/ConfigView.vue）

- 现有 CF provider 切换块条件保持 `v-if="isCloudflare()"`。
- 新增 http 平台块（`!isCloudflare() && !isTauri()`），两个按钮：
  - **"服务器内置 AI"**：仅当 `configStore.serverInfo?.has_env_ai` 时显示；选中后 endpoint / api_key 卡片隐藏（同 CF custom 显隐模式），模型卡片保留、预填服务器 `AI_MODEL`，可点"获取模型"。
  - **"自定义 API"**：默认态，行为与现在完全一致。
- 内置 AI 卡片下方小字提示：由服务器环境变量提供（可含 endpoint 展示）。
- Save / CTA 按钮：`'server'` 时 enabled（`configured` 为 true），保存后正常提示成功。

### 5. 错误处理

- 探测请求失败 → `serverInfo` 为 null，静默退回现状，不弹错。
- `'server'` 下"获取模型"失败 → 复用现有 `configFetchError` 红条展示。
- 用户显式保存的自定义配置不可用 → 行为不变（现状已如此）。

### 6. i18n（frontend/src/i18n/locales.ts）

新增中英键（示例键名）：
- `configServerAi`：服务器内置 AI
- `configServerAiDesc`：由服务器环境变量提供 · {endpoint}
- （无需降级文案：按钮仅在有 env AI 时显示）

### 7. 范围与不做的事

- 涉及文件：`packages/server/src/routes.rs`、`frontend/src/api/http.ts`、`frontend/src/stores/config.ts`、`frontend/src/views/ConfigView.vue`、`frontend/src/i18n/locales.ts`。
- 不涉及：Tauri、CF Worker、Rust core crate、shared types（ServerConfigInfo 定义在 TS 侧即可，或放 shared 视实施方便而定）。
- 无版本号变更。
- 不需要后端 config store 改动。

## 验证

- `cd frontend && pnpm run type-check`
- `cargo build`（server 编译通过）
- 手动验证：
  1. 无环境变量启动服务器 → 配置页无"服务器内置 AI"按钮，行为同现状。
  2. 配 `AI_ENDPOINT` / `AI_API_KEY` / `AI_MODEL` 启动 → 打开应用自动选中内置 AI，GenerateView 无"去配置"提示，生成请求空配置走 env 回退成功；"获取模型"能列出服务器模型。
