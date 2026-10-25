# 片场 Studio · AI 电商短视频工作台

输入产品图 + 商品描述，选择视频风格 Skill，自动生成脚本、分镜图与平台成片。视频优先，图片与文案作为支撑。

当前版本：**v0.4：9 个风格 Skill + 场景基准分镜图 + 单镜重生成 + 成片导出（DeepSeek + REST）**
技术栈：Vue 3 + TypeScript + Vite + Element Plus ｜ Python 3.12 + FastAPI + SQLite ｜ Pillow + FFmpeg ｜ DeepSeek ｜ REST 美术资源

---

## 已经能做什么

- 新建项目、上传产品图、填写商品描述，AI 解析商品档案。
- 9 个视频风格 Skill：痛点反转、开箱测评、产品特写/ASMR、场景生活方式、工厂溯源、教程干货、数码开箱实测、美妆试色、食品食欲感。
- 按品类、目标、平台推荐 Top3 风格，并给出理由；每个 Skill 自带 beats、镜头规范、视觉规范、**场景基准（scene_bible）** 与文案模板。
- 生成结构化脚本与 4–6 个分镜，总时长严格等于 15 秒；脚本、口播、字幕、时长、Prompt 全部可编辑，改哪一镜只重置哪一镜。
- **分镜图**：真实模式逐镜调用 REST 图片模型，按「商品锁定 + 风格锚点 + 场景基准 + 负向词」生成 9:16 无文字分镜图。
- **场景统一**：每个脚本先出 1 张空场景 `scene_anchor`（同机位、同光线、同色板），5 张分镜都以它为环境参考，联系表 `contact_sheet.jpg` 第一格是商品参考、第二格是场景基准。
- **单镜重生成**：每张分镜可单独改画面/Prompt 后只重生成这一张，其它镜头与已出片镜头保留；不再"改一个字全部重跑"。
- **成片**：以分镜图为首帧做图生视频；FFmpeg 统一 1080×1920 / 30fps、烧字幕、加 AI 标识、拼接、生成封面；支持直接下载 MP4 或导出 ZIP。
- 导出 ZIP：MP4、封面、脚本 JSON、SRT、发布文案、manifest。
- 合规检查：极限词/功效词扫描，结果写入 manifest。
- Mock / Real 双模式；真实能力由 LLM / 图片 / 视频三个 provider 决定。
- **DeepSeek LLM**：商品解析、脚本与分镜生成。
- **REST 图生视频**：REST `video_generate` 以分镜图为首帧生成镜头，自动归一化、烧字幕、加 AI 标识。
- **REST 图片生成**：REST `image_generate` 生成每镜无文字分镜图，失败回退 Pillow。
- 单镜失败自动重试一次；REST 镜头限流并发 2 路，5 镜成片从串行 30 分钟压到约 10–20 分钟。
- 已生成的分镜图与镜头会复用，重跑渲染不会重复花钱；点「重新生成」才会覆盖。
- MCP 视频保留为回退；Seedance 与 Ark 仍可选。
- 项目历史、任务中心、设置页、运行环境自检。

> `.env.example` 默认走 DeepSeek + REST；未配置 Key/Token 时会明确报错，不会静默降级。

---

## 快速开始

### 1. 环境要求

- Windows 10/11
- Python 3.12+
- Node.js 20.19+（本项目使用 Node 24）
- 不需要单独安装 FFmpeg：`imageio-ffmpeg` 自带 Windows 二进制

### 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

前端：

```powershell
pnpm --dir web install
```

如果 PowerShell 执行策略拦截 `pnpm.ps1`，用 Node 直接调用：

```powershell
node "<pnpm.mjs 路径>" --dir web install
```

### 3. 构建前端

```powershell
pnpm --dir web build
```

### 4. 启动

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。API 文档：<http://127.0.0.1:8000/docs>。

或使用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

### 5. 开发模式（可选）

```powershell
# 终端 A
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000

# 终端 B
pnpm --dir web dev
```

Vite 会把 `/api` 与 `/media` 代理到 8000。

---

## 使用流程

1. **商品资料**：写项目名与描述，上传 1–9 张产品图；点「保存并解析商品」。
2. **视频风格**：查看推荐 Top3，选择一个 Skill；可先修改商品档案再重新推荐。
3. **脚本分镜**：修改标题、正文、口播、字幕；点「生成预演」。
4. **预演成片**：查看关键帧；确认后点「生成成片」；完成后导出 ZIP。

所有生成任务在后台执行，离开页面不会中断；任务中心可查看进度。

### 一条命令跑通（不打开页面）

```powershell
.\.venv\Scripts\python.exe scripts\pipeline.py `
  --image data\产品图.jpg `
  --name "声波电动牙刷" `
  --description "白色机身，五档模式，IPX7 防水，续航 30 天，128 元"
```

一次跑完：建项目 → 上传图 → DeepSeek 解析 → 推荐风格 → 生成脚本 → 逐镜分镜图 → 图生视频 → 拼成片。
常用参数：

| 参数 | 说明 |
|---|---|
| `--no-video` | 只跑到分镜图，先看联系表再决定是否出片 |
| `--script-id scr_xxx` | 从已有脚本续跑，分镜图与成片复用已生成结果 |
| `--style pain_point_reversal` | 指定风格，默认自动推荐 Top1 |
| `--mock` | 零成本自检，不调外部模型 |

产物：`data/projects/<id>/storyboards/`（分镜图 + 联系表 + review.json）、`clips/`、`videos/`、`covers/`，摘要写入 `data/pipeline_last.json`。

---

## 运行模式与提供方

### Mock

- 商品解析：本地规则（品类关键词、卖点切分、价格提取）。
- 脚本：Skill beats + 模板生成。
- 关键帧/场景板：Pillow 合成商品图、渐变背景。
- 成片：FFmpeg 对关键帧做缓慢推拉，叠加字幕，拼接为 MP4。

### 真实提供方（当前默认）

`.env` 中 `AI_MODE=real`，具体能力由三个 provider 决定：

| 能力 | 变量 | 可选值 | 当前 |
|---|---|---|---|
| LLM | `LLM_PROVIDER` | `mock / deepseek / ark` | `deepseek` |
| 图片 | `IMAGE_PROVIDER` | `mock / rest / ark` | `rest` |
| 视频 | `VIDEO_PROVIDER` | `mock / rest / mcp / seedance` | `rest` |

设置页可以分别切换，保存前会校验对应 Key/Token 是否存在。

#### DeepSeek（LLM）

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=<your-key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

- 负责商品解析（基于商品描述）与脚本/分镜生成。
- 输出严格 JSON，服务端会做时长闭合与字段修复。
- **DeepSeek 是文本模型，不上传商品图**；只有描述也能生成，但视觉细节依赖描述质量。

#### REST 美术资源（图片 + 视频）

```dotenv
IMAGE_PROVIDER=rest
VIDEO_PROVIDER=rest
REST_ART_BASE_URL=https://<your-rest-host>
REST_ART_TOKEN=<your-token>
REST_VIDEO_RESOLUTION=480p
REST_VIDEO_DURATION=4
REST_IMAGE_PARAMS={"aspectRatio":"portrait","quality":"high"}
REST_IMAGE_WAIT_SECONDS=30
REST_VIDEO_WAIT_SECONDS=30
REST_ART_TIMEOUT=1200
```

- 图片：`POST /v1/capabilities/generation/image_generate`，为每个镜头生成**无文字场景板**（可带原图参考）。
- 视频：`POST /v1/capabilities/generation/video_generate`，以场景板 + 原图为参考做图生视频。
- 轮询：`generation_job`；结果：`generation_result`；历史：`generation_list`。
- 视频参数：`aspectRatio=portrait|landscape`、`resolution=480p|768p|1080p`、`duration=1-15`、`seed` 可选。
- `images` 支持 http(s) 或 data URL；第一张作为首帧，第二张为商品参考。
- 返回的 4 秒片段会按镜头时长裁剪/补帧，统一为 1080×1920 / 30fps，叠加字幕与「AI 生成」角标。
- 图片生成失败时自动回退 Pillow 场景板，不阻断整条视频。
- 设置页提供「测试 REST」按钮，只显示历史任务数，不回显 Token。

#### MCP（保留为回退）

```dotenv
VIDEO_PROVIDER=mcp
MCP_VIDEO_URL=https://<your-mcp-host>/mcp
MCP_VIDEO_TOKEN=<your-token>
```

MCP 走 JSON-RPC Streamable HTTP（`initialize` → `tools/call`），工具为 `video_generate / generation_job / generation_result`。上一版已验证 5 镜成片，保留用于对比与回退。

### 安全

- API Key / REST Token / MCP Token 只从 `.env` 读取，不写数据库、不返回前端、不记录日志。
- 设置页只显示“已配置/未配置”和公开 Base URL，不回显密钥。
- 服务默认绑定 `127.0.0.1`；不要在公网直接暴露 8000 端口。

---

## 项目结构

```
backend/
  app/
    main.py              FastAPI 入口、静态托管、异常处理
    config.py            pydantic-settings
    db.py                SQLite 初始化（WAL）
    models.py            9 张表
    schemas.py           请求模型
    api.py               REST 路由
    jobs.py              分镜图 / 渲染 / 真实模式分支
    skills/*.yaml        9 个视频风格（含 scene_bible 场景基准）
    presets/platforms.yaml
    services/            解析、推荐、脚本、分镜、关键帧、FFmpeg、打包、合规
    services/providers/  DeepSeek / REST Art / Ark / Seedance / MCP
  tests/                 pytest
web/
  src/views/             工作台、向导、项目、风格库、任务、设置
  src/components/        SkillCard、GenerationProgress
  src/api.ts             类型化 API 封装
scripts/
  pipeline.py            一条命令跑完 商品图+描述 → 分镜图 → 成片
  smoke.py               端到端 Mock 冒烟
  rest_art.py            REST 图片/视频调用验证
  deepseek_rest_test.py  DeepSeek + REST 单镜集成测试
  render_rest.py         完整 REST 演示渲染
  mcp_probe.py           MCP 工具发现（只读）
  mcp_call.py            通用 MCP 工具调用
  render_mcp.py          完整 MCP 演示渲染
  start.ps1              启动脚本
docs/
  电商宣传物料平台-项目方案.md
data/                    运行时生成（数据库、素材、导出）
```

---

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

覆盖：项目/上传/解析/推荐/脚本/预演/渲染/导出、ZIP 内容、合规词、价格提取。

端到端冒烟：

```powershell
.\.venv\Scripts\python.exe scripts\smoke.py
```

会在 `data/` 下生成一条 15 秒 1080×1920 成片与 `demo_export.zip`。

本次交付已完成两套完整演示，并额外完成 REST 单镜验证：

**Mock 全链路**

- `data/demo/final.mp4`（15.02s · 1080×1920 · 30fps · 含音频轨）
- `data/demo/cover.jpg`
- `data/demo_export.zip`（MP4 + 封面 + 脚本 + SRT + 文案 + manifest）

**真实 MCP 图生视频**

- `data/demo_mcp/final.mp4`（15.03s · 1080×1920 · 30fps · 含音频轨）
- `data/demo_mcp/cover.jpg`
- `data/demo_mcp_export.zip`（manifest 中 `video_provider=mcp`）
- 5 个镜头全部通过 MCP `video_generate` 生成，再归一化、烧字幕、加 AI 标识、拼接

**DeepSeek + REST 单镜验证**

- `data/rest_video_test.mp4`（REST 文生视频：4.46s · 480×864）
- 单镜完整链路：DeepSeek 脚本 → REST `image_generate` 场景板 → REST `video_generate` → 3.0s / 1080×1920 / 30fps
- 5 镜 REST 成片由 `scripts/render_rest.py` 后台生成到 `data/demo_rest/`；网关排队下单镜可能 2–15 分钟，完整 5 镜可能 30 分钟以上

**v0.3 全链路实测（声波电动牙刷，商品图 + 描述 → 分镜图 → 成片）**

- 单条命令：`scripts/pipeline.py --image 牙刷图.jpg --name 声波电动牙刷 --description ...`
- 商品解析 2.2s（DeepSeek）→ 脚本 7.0s（5 镜 / 15s）→ 分镜图 276s（5 张 REST 图 + 联系表）→ 成片 465s（5 镜并发 2 路）
- 产物：`data/projects/prj_d294af2c679d/`
  - `storyboards/`：5 张 1080×1920 分镜图、`contact_sheet.jpg` 联系表、`review.json` 审核清单
  - `clips/`：5 条 1080×1920 / 30fps 镜头（烧字幕 + AI 标识）
  - `videos/scr_7561734288f0.mp4`：15.03s / 1080×1920 / 30fps / 含音轨
  - `covers/cover.jpg`：封面（无叠字）
  - `exports/`：ZIP（MP4 + 封面 + 脚本 + SRT + 文案 + manifest）

---

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 模式、LLM/图片/视频 provider、REST/MCP/FFmpeg/Key 状态 |
| GET | `/api/rest/status` | 测试 REST 连接并返回历史任务数 |
| GET | `/api/mcp/status` | 测试 MCP 连接并返回工具数量 |
| GET/POST | `/api/projects` | 项目列表/创建 |
| POST | `/api/projects/{id}/upload` | 产品图上传 |
| POST | `/api/projects/{id}/analyze` | 商品解析 |
| GET | `/api/skills` | 风格列表 |
| POST | `/api/skills/recommend` | 推荐 Top3 |
| POST | `/api/projects/{id}/scripts` | 生成脚本 |
| PATCH | `/api/scripts/{id}` | 编辑脚本/分镜 |
| POST | `/api/scripts/{id}/previs` | 生成分镜图（`?force=true` 强制重新生成） |
| POST | `/api/shots/{id}/regenerate` | 只重新生成某一张分镜图 |
| POST | `/api/scripts/{id}/render` | 生成成片 |
| GET | `/api/videos/{id}/download` | 直接下载 1080×1920 MP4 |
| GET | `/api/generations/{id}` | 轮询任务 |
| POST | `/api/videos/{id}/export` | 导出 ZIP |
| GET | `/api/exports/{id}/download` | 下载 |

完整请求/响应与错误码见方案文档。

---

## 已知限制（v0.3）

- 只做抖音 9:16 / 15 秒；其他平台只有预设，尚未做变体渲染。
- 不做 TTS 口播与 BGM；成片保留外部模型音轨或静音音轨。
- DeepSeek 是文本模型，不上传商品图；分镜图里的商品细节靠参考图 + 身份锁定句约束，仍可能漂移。
- 分镜图单镜约 30–90 秒；5 镜串行 3–8 分钟，联系表需人工核对商品一致性。
- REST 视频为 480p 上采样到 1080×1920；单镜最低 4 秒，短镜头裁切、长镜头补帧。
- REST 图片输出约 1102×1428（非精确 9:16），会中心裁切到 1080×1920。
- REST 视频单镜耗时受网关排队影响，实测 2–15 分钟不等；已做 2 路并发，5 镜约 10–20 分钟。
- 任务在进程内执行，服务重启会丢失运行中的任务。
- 通配字体仅用于演示；正式商用请替换为 OFL/商用授权字体。
- 未做登录、多用户与权限；默认仅本机使用。

## 下一步

- v0.4：分镜图人工审核门禁（不通过不进入视频）、VLM 一致性打分、REST 任务 webhook、720p/1080p 原生输出、TTS/BGM、多平台变体、SSE。
- v1.0：批量项目、Skill 编辑器、成本看板、素材库。

---

## 方案文档

完整产品与工程方案见 [docs/电商宣传物料平台-项目方案.md](docs/电商宣传物料平台-项目方案.md)。

## 合规说明

生成内容默认标注「AI 生成」，并在 manifest 中保留生成记录。发布前请人工复核商品信息、功效宣称、价格与平台规则；AI 不改变商品本体，但生成画面仍可能与实物存在差异。
