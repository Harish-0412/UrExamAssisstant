# 服务器内置 AI Provider 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Docker/Web 部署时，前端自动探测并使用服务器 `AI_ENDPOINT`/`AI_API_KEY`/`AI_MODEL` 环境变量提供的 AI，打开即用、无需填写配置。

**Architecture:** 后端新增只读端点 `GET /api/config/server` 报告环境变量 AI 是否存在；前端为 http 平台（`!isCloudflare() && !isTauri()`）新增 `'server'` provider（镜像 CF `cf-free` 模式），`loadSaved()` 时自动探测并选中；生成请求发空 endpoint/api_key 由 Axum handler 现有 env 回退逻辑接管。

**Tech Stack:** Rust/Axum 0.8（packages/server）、Vue 3 + Pinia + TypeScript（frontend）。

## Global Constraints

- 涉及文件仅：`packages/server/src/routes.rs`、`packages/server/src/main.rs`、`frontend/src/api/http.ts`、`frontend/src/api/index.ts`、`frontend/src/stores/config.ts`、`frontend/src/views/ConfigView.vue`、`frontend/src/i18n/locales.ts`。
- Tauri / CF Worker / Rust core crate / shared types **零改动**。
- **绝不把 api_key 返回给前端**（server-info 端点只返回 has_env_ai / endpoint / model）。
- 服务器已存自定义配置时，自定义配置优先于自动选中。
- 环境变量回退逻辑已存在于 `packages/server/src/routes.rs:123-125`（generate）、`:237-245`（answer）、`:282-290`（judge）、`:335-343`（explain）、`:49-58`（models）——**不要改动**。
- 无测试套件（AGENTS.md）：验证用 `pnpm run type-check` + `cargo build` + curl 手动验证。
- 无版本号变更、无 i18n 结构变更（只加键）。
- 本仓库无 ESLint/rustfmt 配置，不要运行 lint。

---

### Task 1: 后端端点 GET /api/config/server

**Files:**
- Modify: `packages/server/src/routes.rs`（文件末尾，explain_handler 之后）
- Modify: `packages/server/src/main.rs:56`（/api/config/load 路由之后）

**Interfaces:**
- Produces: `GET /api/config/server` → `Json<ServerConfigInfo>`，其中 `ServerConfigInfo { has_env_ai: bool, endpoint: String, model: String }`（serde Serialize）。

- [ ] **Step 1: 在 routes.rs 追加 handler**

在 `packages/server/src/routes.rs` 末尾（`explain_handler` 之后）追加：

```rust
#[derive(Serialize)]
pub struct ServerConfigInfo {
    pub has_env_ai: bool,
    pub endpoint: String,
    pub model: String,
}

pub async fn server_config_info_handler() -> Json<ServerConfigInfo> {
    let endpoint = ai_endpoint();
    let api_key = ai_api_key();
    let model = ai_model();
    Json(ServerConfigInfo {
        has_env_ai: !endpoint.is_empty() && !api_key.is_empty(),
        endpoint,
        model,
    })
}
```

- [ ] **Step 2: 在 main.rs 注册路由**

`packages/server/src/main.rs:56` 的 `.route("/api/config/load", get(routes::load_config_handler))` 之后追加一行：

```rust
        .route("/api/config/server", get(routes::server_config_info_handler))
```

- [ ] **Step 3: 编译验证**

Run: `cargo build -p exameow-server`
Expected: 编译通过，无 warning 新增。

- [ ] **Step 4: 手动验证端点**

Run（无环境变量）: `AI_ENDPOINT= AI_API_KEY= cargo run -p exameow-server & sleep 2; curl -s localhost:3000/api/config/server; kill %1`
Expected: `{"has_env_ai":false,"endpoint":"","model":""}`

Run（有环境变量）: `AI_ENDPOINT=https://api.openai.com/v1 AI_API_KEY=test AI_MODEL=gpt-4o cargo run -p exameow-server & sleep 2; curl -s localhost:3000/api/config/server; kill %1`
Expected: `{"has_env_ai":true,"endpoint":"https://api.openai.com/v1","model":"gpt-4o"}`

- [ ] **Step 5: Commit**

```bash
git add packages/server/src/routes.rs packages/server/src/main.rs
git commit -m "feat(server): add /api/config/server endpoint exposing env AI presence"
```

---

### Task 2: 前端 API 层（httpApi.getServerInfo + api 分发）

**Files:**
- Modify: `frontend/src/api/http.ts`（第 8 行 interface GenerateResult 附近新增类型；saveConfig 之后新增方法）
- Modify: `frontend/src/api/index.ts`（loadConfig 之后新增分发方法）

**Interfaces:**
- Produces: `httpApi.getServerInfo(): Promise<ServerConfigInfo | null>`；`api.getServerInfo(): Promise<ServerConfigInfo | null>`（Tauri/CF 平台返回 null）；导出类型 `ServerConfigInfo`。

- [ ] **Step 1: http.ts 新增类型与方法**

`frontend/src/api/http.ts` 顶部（`export interface GenerateResult` 附近）新增：

```ts
export interface ServerConfigInfo {
  has_env_ai: boolean
  endpoint: string
  model: string
}
```

文件内 `httpApi` 对象中 `loadConfig` 之后新增：

```ts
  async getServerInfo(): Promise<ServerConfigInfo | null> {
    try {
      const res = await fetch(`${BASE_URL}/api/config/server`)
      if (res.ok) return res.json()
    } catch {}
    return null
  },
```

- [ ] **Step 2: index.ts 新增分发方法**

`frontend/src/api/index.ts` 顶部 import 增加 `ServerConfigInfo` 类型（从 `./http`），`api` 对象 `loadConfig` 之后新增：

```ts
  async getServerInfo(): Promise<ServerConfigInfo | null> {
    if (isTauri() || isCloudflare()) return null
    return httpApi.getServerInfo()
  },
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm run type-check`
Expected: 通过。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/http.ts frontend/src/api/index.ts
git commit -m "feat(frontend): add getServerInfo API for server env AI detection"
```

---

### Task 3: config store 新增 'server' provider

**Files:**
- Modify: `frontend/src/stores/config.ts`（全文多处，见步骤）

**Interfaces:**
- Consumes: `api.getServerInfo()`（Task 2）、`ServerConfigInfo` 类型（Task 2）。
- Produces: `aiProvider` 支持 `'server'`；`serverInfo: Ref<ServerConfigInfo | null>`；`configured`、`loadSaved`、`fetchModels`、`save`、`getConfig`、`setProvider` 的新行为（供 Task 5 ConfigView 使用）。

- [ ] **Step 1: 类型与 import**

`frontend/src/stores/config.ts` 第 11 行：

```ts
export type AIProvider = 'cf-free' | 'custom' | 'server'
```

import 增加：

```ts
import type { ServerConfigInfo } from '@/api/http'
```

- [ ] **Step 2: 新增 serverInfo ref**

`aiProvider` ref 之后新增：

```ts
  const serverInfo = ref<ServerConfigInfo | null>(null)
```

- [ ] **Step 3: configured 支持 http+server**

第 21-29 行替换为：

```ts
  const configured = computed(() => {
    if (!isCloudflare() && !isTauri() && aiProvider.value === 'server') {
      return true
    }
    if (!isCloudflare()) {
      return !!endpoint.value && !!apiKey.value && !!model.value
    }
    if (aiProvider.value === 'cf-free') {
      return !!model.value
    }
    return !!endpoint.value && !!apiKey.value && !!model.value
  })
```

`config.ts` 需要 import `isTauri`（第 5 行改为 `import { isCloudflare, isTauri } from '@/utils/platform'`）。

- [ ] **Step 4: loadSaved 自动探测**

第 31-48 行整体替换为：

```ts
  async function loadSaved() {
    const saved = await api.loadConfig()
    if (saved) {
      if (saved.endpoint) endpoint.value = saved.endpoint
      if (saved.api_key) apiKey.value = saved.api_key
      model.value = saved.model
    }
    if (isCloudflare()) {
      const provider = localStorage.getItem('exameow_ai_provider')
      if (provider === 'custom' || provider === 'cf-free') {
        aiProvider.value = provider
      }
      if (!model.value) {
        endpoint.value = 'cloudflare-worker'
        apiKey.value = 'cloudflare-worker'
      }
      return
    }
    if (isTauri()) return
    serverInfo.value = await api.getServerInfo()
    const storedProvider = localStorage.getItem('exameow_ai_provider')
    if (storedProvider === 'server') {
      aiProvider.value = 'server'
      if (!model.value && serverInfo.value?.model) model.value = serverInfo.value.model
      return
    }
    if (endpoint.value || apiKey.value || model.value) return
    if (serverInfo.value?.has_env_ai) {
      aiProvider.value = 'server'
      if (serverInfo.value.model) model.value = serverInfo.value.model
      localStorage.setItem('exameow_ai_provider', 'server')
    }
  }
```

- [ ] **Step 5: fetchModels 增加 server 分支**

第 50-80 行中，`fetchModels` 函数开头（`isCloudflare() && aiProvider.value === 'cf-free'` 分支之后）插入：

```ts
      if (!isCloudflare() && !isTauri() && aiProvider.value === 'server') {
        models.value = await api.getModels({ endpoint: '', api_key: '', model: '' })
        return
      }
```

- [ ] **Step 6: save 在 server 下只写 localStorage**

第 82-86 行替换为：

```ts
  async function save() {
    localStorage.setItem('exameow_ai_provider', aiProvider.value)
    if (!isCloudflare() && !isTauri() && aiProvider.value === 'server') {
      return
    }
    endpoint.value = normalizeEndpoint(endpoint.value)
    await api.saveConfig({ endpoint: endpoint.value, api_key: apiKey.value, model: model.value })
  }
```

- [ ] **Step 7: getConfig 在 server 下发空配置**

第 88-90 行替换为：

```ts
  function getConfig(): AIConfig {
    if (!isCloudflare() && !isTauri() && aiProvider.value === 'server') {
      return { endpoint: '', api_key: '', model: model.value }
    }
    return { endpoint: endpoint.value, api_key: apiKey.value, model: model.value }
  }
```

- [ ] **Step 8: setProvider 支持 server 预填**

第 92-98 行替换为：

```ts
  function setProvider(provider: AIProvider) {
    aiProvider.value = provider
    models.value = []
    if (provider === 'cf-free' && !model.value) {
      model.value = DEFAULT_CF_MODEL
    }
    if (provider === 'server' && !model.value && serverInfo.value?.model) {
      model.value = serverInfo.value.model
    }
  }
```

- [ ] **Step 9: return 增加 serverInfo**

第 100 行 return 列表增加 `serverInfo`：

```ts
  return { endpoint, apiKey, model, models, loading, configured, aiProvider, serverInfo, loadSaved, fetchModels, save, getConfig, setProvider }
```

- [ ] **Step 10: 类型检查**

Run: `cd frontend && pnpm run type-check`
Expected: 通过。

- [ ] **Step 11: Commit**

```bash
git add frontend/src/stores/config.ts
git commit -m "feat(frontend): add server env AI provider with auto-detection in config store"
```

---

### Task 4: i18n 新增键（9 种语言）

**Files:**
- Modify: `frontend/src/i18n/locales.ts`

**Interfaces:**
- Produces: `configServerAi`、`configServerAiDesc` 两个键，接口声明 + 全部 9 个语言对象（zh/zhTW/en/ja/ko/es/fr/de/ru/ar）。

- [ ] **Step 1: 接口声明**

第 61 行 `configCustomApiDesc: string` 之后新增：

```ts
  configServerAi: string
  configServerAiDesc: string
```

- [ ] **Step 2: 各语言对象添加键**

每个语言对象在 `configCustomApiDesc` 键之后插入：

zh（第 ~511 行）：
```ts
  configServerAi: '服务器内置 AI',
  configServerAiDesc: '使用服务器环境变量提供的 AI 接口，无需填写密钥。',
```

zhTW（第 ~960 行）：
```ts
  configServerAi: '伺服器內建 AI',
  configServerAiDesc: '使用伺服器環境變數提供的 AI 介面，無需填寫金鑰。',
```

en（第 ~1409 行）：
```ts
  configServerAi: 'Server built-in AI',
  configServerAiDesc: 'Uses the AI configured via server environment variables. No API key needed.',
```

ja（第 ~1858 行）：
```ts
  configServerAi: 'サーバー内蔵AI',
  configServerAiDesc: 'サーバーの環境変数で設定されたAIを使用します。APIキーは不要です。',
```

ko（第 ~2307 行）：
```ts
  configServerAi: '서버 내장 AI',
  configServerAiDesc: '서버 환경 변수로 설정된 AI를 사용합니다. API 키가 필요 없습니다.',
```

es（第 ~2756 行）：
```ts
  configServerAi: 'IA integrada del servidor',
  configServerAiDesc: 'Usa la IA configurada mediante variables de entorno del servidor. Sin API key.',
```

fr（第 ~3205 行）：
```ts
  configServerAi: 'IA intégrée au serveur',
  configServerAiDesc: 'Utilise l\'IA configurée via les variables d\'environnement du serveur. Aucune clé requise.',
```

de（第 ~3654 行）：
```ts
  configServerAi: 'Integrierte Server-KI',
  configServerAiDesc: 'Nutzt die über Server-Umgebungsvariablen konfigurierte KI. Kein API-Key nötig.',
```

ru（第 ~4103 行）：
```ts
  configServerAi: 'Встроенный ИИ сервера',
  configServerAiDesc: 'Использует ИИ, настроенный через переменные окружения сервера. Ключ не нужен.',
```

ar（第 ~4552 行）：
```ts
  configServerAi: 'ذكاء اصطناعي مدمج بالخادم',
  configServerAiDesc: 'يستخدم الذكاء الاصطناعي المُعد عبر متغيرات بيئة الخادم. لا حاجة لمفتاح API.',
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm run type-check`
Expected: 通过。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/locales.ts
git commit -m "feat(i18n): add server built-in AI provider labels for 9 locales"
```

---

### Task 5: ConfigView 增加 http 平台 provider 切换

**Files:**
- Modify: `frontend/src/views/ConfigView.vue`

**Interfaces:**
- Consumes: Task 3 的 `configStore.serverInfo`、`configStore.aiProvider`、`setProvider`、`configured`；Task 4 的 i18n 键。

- [ ] **Step 1: script 部分改动**

第 7 行 import 改为：

```ts
import { isCloudflare, isTauri } from '@/utils/platform'
```

第 1 行改为 `import { computed, ref } from 'vue'`。

第 20 行（`configFetching` 之后）新增两个 computed：

```ts
const showEndpointAndAuth = computed(() => {
  if (isTauri()) return true
  if (isCloudflare()) return configStore.aiProvider === 'custom'
  return configStore.aiProvider !== 'server'
})

const fetchModelsDisabled = computed(() => {
  if (isCloudflare()) return configStore.aiProvider === 'custom' && (!configStore.endpoint || !configStore.apiKey)
  if (isTauri()) return !configStore.endpoint || !configStore.apiKey
  if (configStore.aiProvider === 'server') return false
  return !configStore.endpoint || !configStore.apiKey
})
```

- [ ] **Step 2: 新增 http 平台 provider 切换块**

第 81 行（CF 块 `</div>` 之后）插入：

```vue
    <!-- HTTP/Web: Server env AI vs custom API -->
    <div v-if="!isCloudflare() && !isTauri()" class="card-filled p-5 mb-4 shadow-sm border border-[rgb(var(--md-outline-variant)/0.3)]">
      <label class="text-label-md font-semibold block mb-3" style="color: rgb(var(--md-on-surface-variant))">{{ i18n.t('configAiProvider') }}</label>
      <div class="flex items-center gap-3">
        <button
          v-if="configStore.serverInfo?.has_env_ai"
          class="btn-tonal text-sm !px-5 !py-2.5"
          :class="{ 'btn-filled': configStore.aiProvider === 'server' }"
          @click="configStore.setProvider('server')"
        >
          <CpuChipIcon class="w-4 h-4" />
          <span>{{ i18n.t('configServerAi') }}</span>
        </button>
        <button
          class="btn-tonal text-sm !px-5 !py-2.5"
          :class="{ 'btn-filled': configStore.aiProvider !== 'server' }"
          @click="configStore.setProvider('custom')"
        >
          <ServerIcon class="w-4 h-4" />
          <span>{{ i18n.t('configCustomApi') }}</span>
        </button>
      </div>
      <p v-if="configStore.aiProvider === 'server'" class="text-body-sm mt-3" style="color: rgb(var(--md-on-surface-variant))">
        {{ i18n.t('configServerAiDesc') }}<template v-if="configStore.serverInfo?.endpoint"> · {{ configStore.serverInfo.endpoint }}</template>
      </p>
      <p v-else class="text-body-sm mt-3" style="color: rgb(var(--md-on-surface-variant))">
        {{ i18n.t('configCustomApiDesc') }}
      </p>
    </div>
```

- [ ] **Step 3: endpoint/auth 卡片显隐改用它**

第 84 行 `v-if="!isCloudflare() || configStore.aiProvider === 'custom'"` 与第 97 行相同表达式，均改为 `v-if="showEndpointAndAuth"`。

- [ ] **Step 4: 获取模型按钮 disabled 条件**

第 120 行 `:disabled="(!isCloudflare() || configStore.aiProvider === 'custom') && (!configStore.endpoint || !configStore.apiKey)"` 改为 `:disabled="fetchModelsDisabled"`。

- [ ] **Step 5: 类型检查**

Run: `cd frontend && pnpm run type-check`
Expected: 通过。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/ConfigView.vue
git commit -m "feat(frontend): server built-in AI provider toggle in config view"
```

---

### Task 6: 整体验证

**Files:** 无改动。

- [ ] **Step 1: 前端类型检查**

Run: `cd frontend && pnpm run type-check`
Expected: 通过，零错误。

- [ ] **Step 2: 后端编译**

Run: `cargo build`
Expected: workspace 全部编译通过。

- [ ] **Step 3: 手动端到端验证（无 env AI）**

Run: `AI_ENDPOINT= AI_API_KEY= AI_MODEL= cargo run -p exameow-server & sleep 3; curl -s localhost:3000/api/config/server; kill %1`
Expected: `{"has_env_ai":false,...}`；打开 http://localhost:3000 配置页不显示"服务器内置 AI"按钮。

- [ ] **Step 4: 手动端到端验证（有 env AI）**

Run: `AI_ENDPOINT=https://api.openai.com/v1 AI_API_KEY=sk-test AI_MODEL=gpt-4o cargo run -p exameow-server & sleep 3; curl -s localhost:3000/api/config/server; kill %1`
Expected: `{"has_env_ai":true,"endpoint":"https://api.openai.com/v1","model":"gpt-4o"}`；打开 http://localhost:3000 自动选中内置 AI，生成页无"去配置"提示，生成请求（空 endpoint/api_key/model）由服务器 env 回退处理。

- [ ] **Step 5: 确认 git 状态干净**

Run: `git status`
Expected: 工作区干净（仅剩余未提交内容为本任务外文件时注明）。
