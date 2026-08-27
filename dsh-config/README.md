# dsh-config — 整套 RS-A 融合 Agent 配置镜像 (自包含部署包)

> 本目录是 `%USERPROFILE%\.dsh\` 用户层配置的**仓库内镜像**, 使 RS-A 融合
> agent 在新机器可完整重建。运行时以用户目录为准, 本目录是"装机母本"。

## 内容清单

| 文件 | 部署目标 | 作用 |
|---|---|---|
| `settings.yaml` | `~/.dsh/settings.yaml` | LLM 路由 (minimax-cn/MiniMax-M3, 经后端 :8000 代理折叠思考)、默认模型、主题、预设 |
| `profiles/web/` | `~/.dsh/profiles/web/` | Web UI profile (dsh-base + web-app bundles) |
| `profiles/headless/` | `~/.dsh/profiles/headless/` | 无头 profile (探针/CI 用) |
| `profiles/remote-sensing.cordis.yml` | `~/.dsh/profiles/remote-sensing/cordis.yml` | RS-Agent 固化 profile (与 `RS-agent/dsh/remote-sensing.profile.yml` 同源) |
| `../dsh/cordis.patch.yml` | 启动参数 `--patch` | RS-A 品牌补丁 + 插件挂接 (启动时叠加) |
| `../remote-sensing-tools/` | junction → `profiles/node_modules/@rs/` | TS 原生插件五 rs_* 工具 (install-rs-agent.cmd 建 junction) |

## 新机部署顺序

1. `npm i -g @deepseek-ai/dsh`
2. 复制: `settings.yaml` 与 `profiles/*` → `%USERPROFILE%\.dsh\` 对应位置
3. `RS-agent\scripts\install-rs-agent.cmd` (建插件 junction + 固化 profile)
4. `.env` 配 `MINIMAX_API_KEY`; GEE 凭证经后端 `/users/local` 绑定
5. `RS-agent\scripts\start-dsh.cmd` 启动 (自动注入 M3 key + Bearer)

## 凭证安全边界 (铁律)

- `settings.yaml` 的 `apiKeyEnv: MINIMAX_API_KEY` 是**环境变量引用**, 不是明文
  —— 密钥由 `start-dsh.cmd` 启动时注入, 不落任何 yaml
- 插件 token 为 `!!js process.env.REMOTE_SENSING_TOKEN || ''` 运行时注入
- **`~/.dsh/sessions/` 与 `storages/` 是运行时私有数据 (会话历史), 刻意
  不纳入本镜像** —— 不要复制进仓库
