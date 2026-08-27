# remote-sensing-tools（遥感 Agent × dsh 原生插件）

DeepSeek Harness (dsh/Cordis) 原生 TypeScript 插件，替代早期基于 `dsh-mcp-client` 的 stdio 桥接方案（B2-fast）。工具直接经 `fetch` 调用 FastAPI 后端 (`:8000`)，后端为唯一事实源；插件侧不复刻任何业务逻辑。

对应阶段：**B2-full**（五工具原生化）+ **B3**（领域人设注入）+ **B4**(导出审批钩子 + 落盘围栏)。

## 功能

### 五个模型可见工具

| 工具 | 说明 |
| --- | --- |
| `rs_get_dataset_catalog` | 拉取卫星数据集目录（key、GEE 集合 ID、时间/空间分辨率、波段），供主模型按"四分辨率"选数据。`GET /knowledge/catalog` |
| `rs_verify_domain_code` | 提交分析前自查领域代码，返回 PASS 或逐条问题列表。`POST /domain/verify` |
| `rs_execute_analysis` | **阻塞式**执行分析。首轮 `POST /analyze`；收到反问后带原 `task_id` + `clarifications` 经 `POST /tasks/{id}/answer` 续跑；每 3 秒轮询 `GET /tasks/{id}` 直到 need_clarify / done / failed 或超时（默认 10 分钟，硬顶 15 分钟）。need_clarify 返回反问文本，done 返回 caption + task_id，failed 返回人话错误 |
| `rs_get_task_status` | 任务状态即时快照（阶段/反问/结论/错误），用于阻塞超时后补查。`GET /tasks/{id}` |
| `rs_get_result_image` | 下载 done 任务的 JPEG 结果影像并存盘，返回 `{path, bytes}`。`GET /tasks/{id}/result` |

### B3 人设注入

加载时拉取 `GET /prompts/domain`，把 `expert_persona` / `four_resolutions` / `landcover_six_classes` 三段拼为一个 section（含 `prompt_version` 标注，order=10，位于部署 persona 之后、工具指引之前）注册进 `ctx.systemPrompt`。后端不可达时回退到内置快照（2026-08-24 抓取的 v2.0 文本，见 `src/persona.ts` 的 `FALLBACK_DOMAIN_PROMPT`）。可用配置 `personaEnabled: false` 关闭。

### B4 安全机制

1. **导出审批钩子**：`rs_execute_analysis(args.export=true)` 先经 `ctx.approval.request()` 请求人工批准；只有 `allowed-once` 放行。审批服务缺席、无关联 Agent 会话、请求抛错或任何非放行结果一律拒绝执行并给出原因（fail closed）。
2. **落盘围栏**：`rs_get_result_image` 的 `save_path` 强制 `path.resolve` 后必须位于 `workspaceDir`（默认 `D:/rs-agent-workspace`）之内，越界一律拒绝；Windows 下按大小写不敏感比较。
3. **凭证零外泄**：Bearer token 仅在本进程内使用；优先取 `config.token`，留空回退环境变量 `REMOTE_SENSING_TOKEN`。GEE key 永留后端。

## 目录结构

```
remote-sensing-tools/
├── package.json        # @rs/remote-sensing-tools, type: module
├── cordis.yml          # patch 挂载文件
├── src/
│   ├── index.ts        # 插件入口: name/inject/Config/apply
│   ├── backend.ts      # FastAPI HTTP 客户端 (全局 fetch, 无第三方依赖)
│   ├── persona.ts      # B3 人设: 在线拉取 + 内置快照兜底
│   └── tools.ts        # 五个 defineTool 定义 + 审批钩子 + 落盘围栏
└── README.md
```

## 挂载方式

```sh
dsh web --patch D:/rs-agent-workspace/RS-agent/remote-sensing-tools/cordis.yml
```

打开 `http://127.0.0.1:3080` 即可使用。插件以绝对路径 `.ts` 源文件形式由 dsh 内置 loader 加载（与官方"第一个插件"教程同构）；卸载/热替换时全部注册自动清理。

也可把 cordis.yml 内容并入其他 patch 列表（顶层 `- insert:` 格式）。

## 配置项（cordis.yml → config）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `apiBase` | `http://127.0.0.1:8000` | FastAPI 后端地址 |
| `token` | `''` | Bearer token；留空回退读环境变量 `REMOTE_SENSING_TOKEN` |
| `userId` | `rs-a-user` | 固定用户标识 |
| `workspaceDir` | `D:/rs-agent-workspace` | 结果影像允许落盘的根目录（B4 围栏边界） |
| `waitTimeoutMs` | `600000` | rs_execute_analysis 阻塞等待上限，schema 层硬顶 900000；外围策略超时应大于它（本工具自带 timeoutMs = 上限 + 60s 余量） |
| `pollIntervalMs` | `3000` | 任务状态轮询间隔（≥1000ms） |
| `personaEnabled` | `true` | 是否注入遥感领域人设 section |
| `hideContextInjection` | `true` | UI 润色：隐藏会话流中的「上下文注入」行（仅 web 组合生效；类名绑定 dsh 0.1.1-rc.2 构建哈希，升级后复核 `src/ui-polish.ts`） |

## 验证步骤

1. 启动后端：`RS-agent/scripts/start-backend.cmd`，确认 `curl http://127.0.0.1:8000/prompts/domain` 返回 v2.0 三段文本。
2. 注入 token 环境变量后按上文挂载 dsh，启动日志无插件报错。
3. Web UI 对话验证：
   - "调用 rs_get_dataset_catalog 看看有哪些数据集" → 返回数据集清单（后端该端点就绪后）；
   - "分析北京 2024 年夏季植被覆盖" → 触发 rs_execute_analysis，等待期间可见 running；如后端反问则模型转述问题，回答后自动续跑；done 后返回结论；
   - "把刚才的结果图保存下来" → rs_get_result_image 落盘成功；尝试给 `C:/tmp/x.jpg` 之类越界路径应被拒绝；
   - "导出这份分析结果"（export=true）→ 应弹出人工审批；拒绝或审批服务缺席时工具返回拒绝说明。
4. 类型校验：在插件目录执行 `npx tsc --noEmit`（见下方开发校验）。

## 开发校验

```sh
cd D:/rs-agent-workspace/RS-agent/remote-sensing-tools
npm run typecheck    # tsc --noEmit, 零错误
```

类型解析依赖 `node_modules/@deepseek-ai/*` 到全局 dsh 安装目录的 junction（见 `setup-dev-links.cmd`），运行时不需要它们——dsh loader 自行解析 `@deepseek-ai/*` 导入。
