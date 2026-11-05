# 企业级 RAG 智能文档问答系统

面向企业的知识库检索增强生成（RAG）系统：上传文档即可自动解析、分块、向量化入库，并基于知识库进行带引用溯源的流式问答。

系统分为两端，同一个后端服务同时托管：

| 端 | 访问路径 | 目录 | 定位 |
|---|---|---|---|
mingc | 用户端（GALA 智能问答助手） | `/` | `user/` | 可嵌入任意官网，普通访客直接提问，不暴露任何技术参数 |
| 管理端（后台管理） | `/admin/` | `frontend/` | 上传文档、问答记录存档，以及**模型 / 检索参数配置** |

- 大模型（Key / URL / 模型名 / 温度）、向量化模型、Tavily Key、检索与分块参数均可在管理端「设置 → 模型服务」修改，**保存后立即生效**；`backend/.env` 作为默认回退
- 本地零成本运行、云端一键迁移：业务库与向量库共用同一套抽象，改一个连接串即可切换
- 初始知识库随仓库提交，服务启动自动导入 `DOC/` 下的企业文档

> **导航**：[启动方法](#启动方法本地) · [RAG 工作原理](#rag-工作原理一图看懂) · [配置项速查表](#配置项速查表小白必读) · [目录结构](#目录结构) · [数据存储](#数据存储与数据库) · [常见问题与排查](#常见问题与排查faq) · [回答慢怎么优化](#c-回答慢从哪慢怎么快)

## RAG 工作原理（一图看懂）

RAG（Retrieval-Augmented Generation，检索增强生成）= **先查资料，再让大模型照着资料回答**。它解决的是大模型「不知道你公司内部文档」和「容易编造」的问题：回答前先从你的知识库中检索最相关的原文片段，把这些片段塞进 Prompt，要求模型只能依据这些内容作答。

本项目把 RAG 拆成两条互不影响的链路：

```
【链路 A · 入库（离线，上传或启动时执行一次）】
文档 PDF/DOCX/PPTX/TXT/MD
   │  ① 解析抽取
   ▼
纯文本 ──② 清洗（去空白/乱码/过短片段）──▶ ③ 中文分块（600 字 / 重叠 120）
   │                                            │
   │                                     ④ 向量化（BGE → 512 维向量）
   ▼                                            ▼
documents 表（元数据、状态）        ⑤ 写入向量表 vector_chunks（文本 + 向量 + 来源）

【链路 B · 问答（在线，每次提问执行）】
用户问题
   │  ① 问题向量化（同一个 BGE 模型）
   ▼
② 向量检索：余弦相似度取 Top-K（默认 5 条），低于阈值（默认 0.35）的丢弃
   │
   ▼
③ 拼接上下文：[1]（文件名）原文… [2]…
   │
   ▼
④ 构建 Prompt：SYSTEM_PROMPT（只能依据上下文回答 + 标注来源） + 用户问题
   │
   ▼
⑤ 调用大模型（百炼 / DeepSeek）→ SSE 逐字流式返回 → 回答与来源落库
```

### 原理五步在本项目中的落点

| 通用 RAG 步骤 | 本项目实现 | 关键文件 |
|---|---|---|
| 1. 加载文档 | 按扩展名分派解析器，WPS 异常 docx 自动回退 OOXML | `backend/app/services/parser.py` |
| 2. 切割文本 | 递归分块，中文分隔符优先级 `\n\n → \n → 。！？ → ；， → 空格` | `backend/app/services/chunker.py` |
| 3. 向量化 | 本地 BGE（默认）／OpenAI 兼容 Embedding API／hash（离线联调） | `backend/app/services/embeddings.py` |
| 4. 构建提示词 | `SYSTEM_PROMPT` 拼装检索上下文，强制引用与反幻觉 | `backend/app/services/llm.py` |
| 5. 调用大模型 | OpenAI 兼容协议，`stream=True` 流式输出 | `backend/app/services/llm.py` 的 `stream_chat()` |

> 想看到每一步对应的函数名与行号？见 [RAG 实际执行流程](#rag-实际执行流程)。

### 配置参数分别影响哪一步

| 参数（`backend/.env`） | 影响的环节 | 改动后的后果 |
|---|---|---|
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | A③ 分块 | 只对**新上传**文档生效；已入库切片不会自动重切 |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | A④ 与 B① 向量化 | 换模型会改变向量维度，**必须清库重新导入** |
| `TOP_K` | B② 检索 | 界面「参考来源」最多显示几条（默认 5） |
| `SCORE_THRESHOLD` | B② 检索 | 低于阈值的切片被丢弃；设 `0` 等于不过滤，寒暄也会命中无关内容 |
| `LLM_MODEL` | B⑤ 生成 | 只改这一项即可换模型，须与 Key 所属厂商一致 |
| `LLM_TEMPERATURE` | B⑤ 生成 | 越小越稳定收敛（默认 0.3），越大越发散 |
| `SYSTEM_PROMPT`（`llm.py`） | B④ 提示词 | 控制回答风格、引用格式、兜底话术；改后必须重启 |

**两个关键直觉**：

- **检索没命中，就不会调用大模型**。当所有切片都低于 `SCORE_THRESHOLD` 时，`rag.py` 直接返回写死的兜底文案，此时改 Prompt 不会有任何变化。
- **入库和问答用的是同一个向量化模型**。本地换 Embedding 模型后，旧向量与新问题向量不在同一空间，必须删除文档重新导入。

## 技术栈

| 层 | 本地模式（默认） | 云端模式（免费） |
|---|---|---|
| 前端 | Vue 3 + Vite（管理端）/ 原生 HTML（用户端） | 同左，构建后由后端单服务托管 |
| 后端 | Python 3.10+ / FastAPI | 同左 |
| 业务数据库 | SQLite（文件落盘） | PostgreSQL（Neon / Supabase 免费层） |
| 向量库 | 内置 SQLite 向量表（零依赖） | pgvector（与业务库同库） |
| 向量化 | BAAI/bge-large-zh-v1.5（本地） | OpenAI 兼容 Embedding API（SiliconFlow / 智谱等） |
| 生成 | 阿里云百炼 / DeepSeek（OpenAI 兼容协议） | 同左 |
| 文档解析 | PyMuPDF / python-docx / python-pptx | 同左 |
| 分块 | LangChain RecursiveCharacterTextSplitter（记录原文 `char_start` / `char_end`） | 同左 |
| 原文预览与引用定位 | mammoth（docx→HTML）/ markdown / python-pptx 按页渲染；召回后按问题关键词从切片中抽取细粒度 span，预览页按原格式高亮 | 同左 |

> 可选：`VECTOR_BACKEND=chroma` 可切换为 ChromaDB 本地持久化向量库（需安装 chromadb）。

## 功能模块

### 用户端 · GALA 智能问答助手（`user/index.html`）
- 品牌名统一为 **GALA 智能问答助手**，专属 SVG logo（对话气泡），同时用于浏览器标签页 favicon
- 单页极简聊天界面，可直接通过 `<iframe>` 嵌入官网任意位置
- SSE 流式回答，逐字输出，带"参考来源"卡片（文件名 + 相似度；联网结果标注「联网」并可点击跳转原网页）
- 聊天窗口默认居中；**点击回答正文中的 `[n]`（或来源卡片文件名）时才展开右侧文档面板**，并按导入时的原格式渲染（Word 保留文档结构、Markdown 渲染标题/列表/表格、PPT 按页、TXT 保留排版、PDF 用内置阅读器），自动滚动并**精准高亮支撑该回答的原文句子**（切片入库时保存字符偏移，召回后再按问题关键词挑出细粒度 span 绑定引用，不再整段切片定位）
- 点击不同序号可在右侧切换文档与定位片段；不再提供「打开原文」入口（窄屏下面板全屏显示，可点 × 收起，收起后恢复居中）
- 无需登录、无模型/参数配置项，访客打开即用
- 每个访客自动生成独立会话，全部落库供管理端查看

### 管理端 · 智能问答助手（`frontend/src/App.vue`）
- 侧边栏首个入口，内嵌用户端聊天页（`/user/index.html?source=admin`），管理员可在后台直接试问
- 与用户端共用同一套问答链路，但请求走 `/api/chat`，会话来源记为 **管理**（访客名「管理员」），会出现在「问答记录」中
- 打开方式：`/admin/` 点击左侧「智能问答助手」，或直接访问 `/admin/?tab=assistant`

### 管理端 · 文档管理（`frontend/src/views/DocumentsView.vue`）
- 拖拽或点击上传：PDF / DOCX / PPTX / TXT / Markdown
- 上传进度、处理状态（处理中 / 已就绪 / 失败，失败可见原因）
- 列表展示：类型、大小、切片数、分类、上传时间
- 按文件名搜索、按分类/状态筛选
- 切片预览（查看分块原文与序号）
- 行内修改分类、删除文档（同步清除向量切片）
- 顶部统计：文档数 / 切片数 / 存储占用 / 处理状态分布
- 一键导入 `DOC/` 初始文档（按文件哈希去重）

### 管理端 · 问答记录（`frontend/src/views/SessionsView.vue`）
- 统计卡片：总会话、用户端会话、管理端会话、总消息数
- **问答自动归档**：每个用户问题按关键词规则自动归入唯一分类，默认 6 类 —— 故障异常 / 账号权限 / 数据报表 / 操作指引 / 功能咨询 / 其他（分类与关键词可在「设置」模块中增删改）
- 分类筛选条：显示各分类会话数量，点击即筛选（可再次点击取消）
- 会话列表：标题、分类标签、来源标记（用户 / 管理）、访客名、消息数、时间
- 按来源筛选、按标题搜索
- 点击会话查看完整问答往来（含引用来源）
- **每 10 秒自动刷新**，另有「刷新」按钮：用户端产生的问答无需手动重载页面即可出现

> 分类规则位于 `backend/app/services/category_service.py`：按分类的 `sort_order`（越小优先级越高）逐类匹配关键词，命中即返回，保证一个问题只归一类。分类数据存于 `qa_categories` 表，可在管理端「设置」中增删改；历史会话在服务启动时自动补齐分类（`crud.backfill_session_categories`），删除某分类时其历史会话自动回落到「其他」。

### 管理端 · 设置（`frontend/src/views/SettingsView.vue`）
顶部为横向子标签：**模型服务 / 联网搜索 / 文档分类 / 问答分类 / 系统信息**，一次只显示一页，避免内容过长。
- **模型服务（核心）**：四组表单，保存后立即生效，无需重启
  - 大模型：API Key、Base URL、模型名、温度；内置「阿里云百炼 / DeepSeek / 硅基流动」快捷填充，也可填任意 OpenAI 兼容厂商
  - 向量化：方式（local / openai / hash）、接口地址、模型名、专用 Key（留空自动复用大模型 Key）、本地模型名、批量大小
  - 联网搜索：Tavily API Key 与接口地址
  - 检索与分块：分块大小、重叠、召回条数、相似度阈值
  - 密钥输入框为密码类型，**默认回显系统已有的 Key**（管理端保存过的优先，其次 `backend/.env`，都没有则为空）；可直接改后保存，**清空后保存即删除该 Key**（对应模型调用会因缺 Key 而失败）
- **联网搜索（Tavily）**：开 / 关、搜索时机（`always` 每次提问都联网 / `fallback` 仅本地知识库未命中时联网）、返回网页条数（1~10）；保存后立即生效
- **文档分类（知识库）**：编辑分类名称、颜色、说明，支持新增/删除；删除某分类会把其下文档变为「未分类」。与「问答分类」是两套独立体系
- **问答分类**：表格化编辑顺序（优先级）、名称、颜色、关键词、启用状态，支持新增与删除；「其他」为兜底分类，不可删除
- **系统信息（只读）**：当前生效的大模型 / 接口地址 / 向量化方式与模型、数据库、向量库后端、各类 Key 是否已配置、单文件上限与数据目录
- **重建向量索引**：修改向量化方式/模型名或分块大小/重叠后，点「保存模型与检索配置」会**自动按新配置重新切片并向量化全部文档**（`PUT /api/settings`）；重建失败时返回具体原因（前端提示「保存失败：重建文档分块失败（文件名：原因）」），可修正后重试或点「立即重建向量索引」（`POST /api/settings/rebuild-vectors`）

## 目录结构

```
RAG/
├── start.bat                         # Windows 一键启动入口（双击即可）
├── restart.bat                       # 一键重启后端：自动结束旧后端并启动（改代码后用）
├── scripts/
│   └── start.ps1                     # 启动逻辑：选解释器 → 查 .env → 构建前端 → 端口预检 → 启动
├── backend/
│   ├── .env.example                  # 配置模板（复制为 .env 后填密钥）
│   ├── .env                          # 实际配置（已被 .gitignore 排除，不提交）
│   ├── requirements.txt              # 通用依赖（含本地 BGE / Chroma）
│   ├── requirements-cloud.txt        # 云端精简依赖（pgvector + API 向量化）
│   ├── venv/                         # 本地虚拟环境（可选，已被 .gitignore 排除）
│   ├── data/                         # 运行时生成（已被 .gitignore 排除）
│   │   ├── rag.db                    # SQLite 业务库 + 向量表（含 -wal / -shm）
│   │   ├── uploads/                  # 上传的原始文件
│   │   └── chroma/                   # 可选 ChromaDB 持久化目录
│   └── app/
│       ├── main.py                   # FastAPI 入口（建表/初始导入/双端静态托管/端口预检/预热）
│       ├── config.py                 # 全部可配置项（.env 默认值集中在此）
│       ├── models.py                 # ORM 模型（分类/文档/会话/消息/设置，文档含 preview_html）
│       ├── db.py                     # SQLAlchemy 引擎（SQLite / PostgreSQL）+ 旧表补列迁移
│       ├── crud.py                   # 数据访问层
│       ├── routers/
│       │   ├── documents.py          # 上传/列表/切片/原文预览（/doc/{id} 按原格式渲染并高亮引用片段）/改分类/删除/统计
│       │   ├── knowledge.py          # 分类管理 + 知识库总览
│       │   ├── chat.py               # SSE 问答 + 会话（含 /api/public/chat 用户端入口）
│       │   ├── qa_categories.py      # 问答归档分类增删改（设置模块使用）
│       │   └── settings.py           # 运行时设置（分块/向量化变化时保存即重建切片）+ 初始导入
│       └── services/
│           ├── parser.py             # 解析清洗（docx 非法包自动回退 OOXML）
│           ├── chunker.py            # 递归分块，并记录每段在原文中的 char_start / char_end
│           ├── doc_preview.py        # 原文件渲染 HTML（docx / markdown / pptx / txt，超大图片转占位）
│           ├── span.py               # 从召回切片中挑出真正支撑回答的细粒度原文句子（span）
│           ├── embeddings.py         # 向量化：local / openai / hash（客户端复用）
│           ├── vector/               # 向量库抽象：sqlite / pgvector / chroma（切片、字符偏移、向量）
│           ├── ingest.py             # 入库流水线（失败回滚）+ 单篇重切 / 全量重建索引
│           ├── seed.py               # DOC 初始文档自动导入
│           ├── rag.py                # 检索 → 上下文（统一引用编号 + 绑定 span）→ 流式生成 → 兜底文案
│           ├── llm.py                # LLM 客户端（连接复用）+ 系统提示词（模型/Key 读运行时配置）
│           ├── web_search.py         # Tavily 联网搜索（未配 Key 自动跳过）
│           ├── category_service.py   # 问答自动归档分类（DB 驱动，支持增删改）
│           └── settings_service.py   # 运行时配置读写（DB 覆盖 .env，密钥回显当前值）
├── frontend/                         # 管理端源码（构建产物由后端托管在 /admin/）
│   ├── index.html                    # 管理端页面标题
│   ├── vite.config.js                # 开发端口读取 backend/.env 的 PORT（被占用时顺延）+ 代理到后端
│   ├── dist/                         # 构建产物（npm run build 生成）
│   └── src/
│       ├── main.js                   # Vue 入口
│       ├── App.vue                   # 左侧导航 + 模块容器（智能问答助手 / 文档管理 / 问答记录 / 设置）
│       ├── api.js                    # 统一请求封装
│       ├── style.css                 # 蓝色企业主题变量
│       └── views/                    # DocumentsView.vue / SessionsView.vue / SettingsView.vue
├── user/
│   └── index.html                    # 用户端单文件：默认居中；点击引用 [n] 展开右侧原格式文档并精准高亮
├── DOC/                              # 本地初始知识库（已被 .gitignore/.vercelignore 排除，不提交/不上云；本地启动自动导入）
├── api/
│   └── index.py                      # Vercel Serverless 入口（导出 backend 的 FastAPI app）
├── requirements.txt                  # Vercel Python 函数依赖（= backend/requirements-cloud.txt）
├── vercel.json                       # Vercel 构建/路由配置（构建前端 + 全量交给 FastAPI）
├── .vercelignore                     # Vercel 上传忽略（排除 venv / data / dist 等）
├── tests/
│   ├── smoke_test.py                 # 冒烟测试（需先启动后端）
│   └── sample_handbook.md            # 冒烟测试用示例文档
├── Dockerfile                        # 单镜像同时打包后端 + 管理端 + 用户端
├── docker-compose.yml                # 本地验证云端链路（Postgres + pgvector + 应用）
└── README.md
```

## 配置项速查

配置分四层，**详细注释都写在代码与配置模板里**，README 只做索引，不重复维护：

| 层级 | 位置 | 改后如何生效 |
|---|---|---|
| 运行时设置（**大模型 / 向量化 / Tavily Key / 检索与分块 / 联网开关 / 分类**） | 管理端「设置 → 模型服务」→ 存 `app_settings` / `qa_categories` | **保存即生效**；分块大小/重叠或向量化配置变更时，保存会**自动重建全部文档切片**，失败会提示具体原因 |
| 环境变量（端口、数据库、向量库、HuggingFace 离线…） | `backend/app/config.py`（每项均有注释）+ `backend/.env.example`（分组说明） | 复制为 `backend/.env` 修改后**重启后端** |
| Prompt、兜底文案、业务常量 | `backend/app/services/llm.py`（`SYSTEM_PROMPT`）、`rag.py` | **重启后端** |
| 前端页面与样式 | 用户端 `user/index.html`（HTML/CSS/JS 单文件）；管理端 `frontend/src/` | 用户端刷新页面；管理端需 `npm run build` |

> 完整环境变量清单与作用说明见 `backend/.env.example` 与 `backend/app/config.py` 的注释。
>
> **优先级**：管理端保存的值（`app_settings` 表）> `backend/.env` 默认值。设置页会回显密钥的**当前生效值**（管理端保存过优先，否则取 `.env`），未配置时为空，方便确认已有 Key 并直接修改。

> **默认不带任何 Key**：`backend/.env` 与 `.env.example` 中的密钥均为空值，首次启动后需在管理端「设置 → 模型服务」填写大模型 Key（向量化 Key 可留空复用）；未填写时问答会提示缺少 Key。

> **密钥安全提醒**：`backend/.env` 已被 `.gitignore` 排除，不会提交到 Git。代码与 `.env.example` 中的密钥均为空；管理端保存的密钥存在 `backend/data/rag.db`（本地）或云端数据库中。设置页打开时会把当前生效的 Key 回显到密码输入框（仅管理端内部使用），因此**不要把管理端暴露到公网**。

**切换大模型**（管理端「设置 → 模型服务 → 大模型」）：填 API Key、Base URL、模型名后保存，下一次提问立即使用新模型。页面内置三个快捷填充：

| 厂商 | Base URL | 常用模型 |
|---|---|---|
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |

> 兜底方式：不填管理端配置时，仍按 `backend/.env` 的 `DASHCOPE_*` / `DEEPSEEK_*` 读取。厂商必须与 Base URL 匹配，否则 401 / 404。

> **回答慢优先换小模型**：模型选型对首字延迟影响最大。`qwen-plus` 首字约 0.5 秒，超大模型首字可到 2 秒以上，且流式总时长随生成长度线性增加。完整的提速顺序（换模型 → 向量化改在线 → 调 `SCORE_THRESHOLD` / `TOP_K` → 精简 Prompt）见 [C. 回答慢：从哪慢、怎么快](#c-回答慢从哪慢怎么快)。

### 本地向量化改在线（无需本地模型，启动更快）

在管理端「设置 → 模型服务 → 向量化」中操作，**不用重复填 Key**：向量化 Key 留空时会自动复用大模型 Key（同属 OpenAI 兼容协议）。

| 字段 | 建议值 |
|---|---|
| 向量化方式 | `openai` |
| 接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1`（或硅基流动等） |
| 向量模型名 | `qwen3.7-text-embedding-flash` / `text-embedding-v4` / `BAAI/bge-m3` |
| 批量大小 | 10（百炼单请求最多 20 条） |

等价的 `.env` 兜底写法（不填管理端配置时生效）：

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_MODEL=qwen3.7-text-embedding-flash
EMBEDDING_BATCH_SIZE=10
```

**切换后必须重建向量索引**：不同模型的向量维度不同（本地 `bge-small` 512 维、百炼 Embedding 1024 维），旧向量与新问题向量不在同一空间，直接检索会全部不匹配。三种做法：

| 做法 | 操作 | 影响 |
|---|---|---|
| 保存即重建（推荐） | 在「设置 → 模型服务」改向量化配置或分块大小/重叠后点「保存模型与检索配置」 | 后端立即按新配置重新切片并向量化全部文档；失败会返回「保存失败：重建文档分块失败（文件名：原因）」 |
| 手动重试 | 重建失败后点「立即重建向量索引」 | 保留会话记录与文档；原文件缺失的文档需重新上传 |
| 全量重建 | 停服务 → 删除 `backend/data/rag.db`、`rag.db-wal`、`rag.db-shm` → 重启 | 上传文档与会话记录会清空 |
| 逐篇重导 | 管理端删除文档后重新上传，或调用 `POST /api/settings/seed` | 保留会话记录，需逐篇重传 |

> 判断是否已切换成功：管理端「设置 → 系统信息」的「向量化方式」应显示 `openai`，向量模型显示你配置的在线模型名。


### 代码内配置

| 想改什么 | 位置（详细说明见代码注释） |
|---|---|
| 系统提示词 Prompt / 兜底话术 | `backend/app/services/llm.py`（`SYSTEM_PROMPT`）、`backend/app/services/rag.py`（`stream_answer`） |
| 大模型 / 向量化 / Tavily / 检索参数 | 管理端「设置 → 模型服务」（推荐）；默认值在 `backend/.env` |
| 运行时设置项定义（类型、范围、分组、是否密钥） | `backend/app/services/settings_service.py`（`SPECS`） |
| 问答归档分类默认规则 | `backend/app/services/category_service.py`（`DEFAULT_CATEGORIES`）；日常在管理端「设置」里改 |

#### 助手回复格式（在哪改、怎么改）

回答的格式、语气、引用方式**全部由系统提示词决定**，唯一修改位置是 [llm.py](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/app/services/llm.py#L12-L23) 的 `SYSTEM_PROMPT`（模块级常量，**改完必须重启后端**）。

**联网与未联网共用同一套 Prompt**，所以两种场景的回复格式一致，不按来源区分。当前规则：

| 规则 | 内容 |
|---|---|
| 纯文本 | 不用 `**`、反引号、`#`、`-` 等 Markdown 标记（用户端按 `white-space: pre-wrap` 原样渲染） |
| 结构 | 先一句话结论，再分点说明（`1. 2. 3.`） |
| 引用 | 句末标注 `[编号]`，与参考上下文编号一致；不写「（来源：…）」「原文依据」`[[n]]`，不输出网址 |
| 合并来源 | 多个来源按内容逻辑组织，不按来源类型分段 |
| 兜底 | 无依据时统一回复「【知识库中未查询到相关内容】」 |

| 想改什么 | 改哪一句 |
|---|---|
| 角色定位 | 首行「你只能使用下面参考上下文回答问题…」 |
| 无答案时的兜底话术 | 第 5 条 `【知识库中未查询到相关内容】` |
| 回答详略 / 结构（先结论后分点等） | 第 2 条 |
| 引用格式 | 第 3 条 |

示例：把回答改成「先结论、再分点列出依据」：

```python
SYSTEM_PROMPT = """你只能使用下面参考上下文回答问题，禁止编造上下文不存在的信息。
参考上下文每条以 [编号] 开头，该编号就是引用编号：
{context}
用户问题：{question}
回答要求（无论内容来自知识库还是联网搜索，都按同一格式）：
1. 纯文本作答，不要使用 Markdown 标记（如 **加粗**、`反引号`、# 标题、- 列表符）
2. 结构：先用一句话给出结论，再分点说明，分点用「1. 2. 3.」编号
3. 引用：在对应句子末尾标注 [编号]，编号与参考上下文一致；不要写「（来源：…）」「原文依据」「[[编号]]」，也不要输出网址
4. 多个来源合并作答，按内容逻辑组织，不要按来源类型分段
5. 上下文没有答案时，只回复：【知识库中未查询到相关内容】，不要猜测"""
```

> 另有一处**不在 Prompt 里**的文案：检索完全未命中、且未联网或联网也无结果时，`rag.py` 直接返回写死的兜底话术（[stream_answer()](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/app/services/rag.py#L106-L112)），当前文案与 Prompt 的兜底保持一致；「参考来源」卡片样式在 `user/index.html` 与 `frontend/src/`。
> 改完 Prompt 仍无变化？先看下一节「Prompt 改了，为什么回复还是老样子？」。

#### 参数生效链路（优先级很重要）

```
每次提问 → settings_service.get_runtime()
              ├─ 启动时读 .env 默认值（LLM / EMBEDDING / TAVILY / TOP_K / CHUNK…）
              └─ 再用 app_settings 表里的同名值覆盖
```

- **管理端保存值优先于 `.env`**。在「设置 → 模型服务」保存后，运行时会立即刷新；`.env` 只在未保存过该项时生效。
- 覆盖范围：大模型（Key / Base URL / 模型名 / 温度）、向量化（方式 / 地址 / 模型 / Key / 批量）、Tavily（Key / 地址）、检索与分块（`TOP_K` / `SCORE_THRESHOLD` / `CHUNK_SIZE` / `CHUNK_OVERLAP`）、联网开关。
- `CHUNK_SIZE` / `CHUNK_OVERLAP` 影响**新上传**的文档；已入库切片要在设置页改分块参数并保存（保存时会自动按新参数重切并重建向量），或点「立即重建向量索引」/重新上传。
- 密钥字段默认回显当前生效值（管理端保存值 > `.env`，都没有为空）；**清空并保存即删除该项 Key**（会覆盖 `.env` 默认值，未改动的密钥不会被重置）。

#### Prompt 改了，为什么回复还是老样子？

有**三个**原因会导致改 Prompt 后看不到变化，按顺序排查：

**① 这次回答根本没调用大模型（最常见）**

当检索结果全部低于 `SCORE_THRESHOLD`（默认 `0.35`）时，系统走的是**代码里写死的兜底文案**，而不是 LLM：

```python
# backend/app/services/rag.py : L66-L72
if not context:
    yield {
        "type": "token",
        "content": "很抱歉未找到相关结论。请问当前系统相关问题或换一种问法。",
    }
```

所以问"你是谁""你好"这类寒暄，或问题与文档无关时，返回的那句话来自 `rag.py`，跟 Prompt 无关。想验证 Prompt 是否生效，请提一个**能命中知识库**的具体问题（如"APP 用户端有哪些核心功能"），观察回答的语气/格式是否变化。

> 顺带：这句话本身也可改，位置在 `backend/app/services/rag.py` 的 `stream_answer()` 内。

**② 后端没有重启**

`SYSTEM_PROMPT` 是模块级常量，只在进程启动时加载一次。改 `llm.py` 后必须重启后端（`Ctrl+C` 后重新 `python -m app.main`），热更新不会生效。若有多个终端/旧进程在跑，先确认 8080 端口上的进程是新的。

**③ 改的不是运行时实际使用的 Prompt**

本项目 Prompt 只有一处：`backend/app/services/llm.py` 的 `SYSTEM_PROMPT`，由 `stream_chat()` 调用。若你在其他地方（旧的 `SettingsView.vue`、数据库 `app_settings.system_prompt`）改过，那已经不是当前版本的系统行为，不会再被读取。

**快速自检清单**：

| 检查项 | 期望值 |
|---|---|
| 提问内容是否命中知识库 | 用户端回答上方出现"📎 参考来源"卡片；有卡片才说明调用了 LLM |
| 后端是否已重启 | 启动日志时间晚于 `llm.py` 的修改时间 |
| 生效的 Prompt 位置 | `backend/app/services/llm.py` 的 `SYSTEM_PROMPT`（全文只有这一个） |
| 检索阈值 | `.env` 的 `SCORE_THRESHOLD`；调低（如 `0.2`）可让更多问题进入 LLM 分支 |

### 前端页面与样式

- **用户端**：`RAG/user/index.html` —— 项目根目录下的 `user/` 文件夹（不是 `frontend/`），HTML/CSS/JS 全在一个文件里。推荐问题在 `#suggestions`，欢迎语在 `#welcome`，配色在 `<style>` 块（主题蓝 `#165dff`）。改完**刷新页面**即可，无需构建。
- **管理端**：源码在 `frontend/src/`；导航布局 `App.vue`，三个页面在 `views/`，全局主题变量在 `style.css` 的 `:root`，接口封装在 `api.js`。改完需 `cd frontend && npm run build`，再刷新 `/admin/`（必要时 `Ctrl+F5`）。
- 页面标题：管理端 [frontend/index.html](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/frontend/index.html)，用户端 `user/index.html` 的 `<title>`。

**用户端底部默认问题**的代码长这样（改文字即可，`ask()` 里的内容就是点击后发送给客服的问题）：

```html
<!-- user/index.html : L164 -->
<div class="suggestions" id="suggestions">
  <button onclick="ask('APP 用户端有哪些核心功能？')">APP 用户端有哪些核心功能？</button>
  <button onclick="ask('如何创建直播间？')">如何创建直播间？</button>
  <button onclick="ask('后台管理系统能做什么？')">后台管理系统能做什么？</button>
</div>
```

- 用户发出第一条消息后，推荐问题区域会自动隐藏（`user/index.html` 的 `addMsg()` 中处理）

> 改完代码如何更新、如何停止/重启服务，见下文[「停止 / 重启服务」](#停止--重启服务改代码后怎么更新)。

## 环境要求

| 组件 | 要求 | 说明 |
|---|---|---|
| Python | **3.10 – 3.12（推荐 3.11）** | 3.13/3.14 下 `chromadb` 存在兼容问题；若只用默认 SQLite 向量后端可运行，但不建议 |
| Node.js | 18+（推荐 20 LTS） | 仅构建/开发管理端时需要，纯后端运行可不装 |
| 内存 | 本地 BGE 模型建议 ≥ 4GB 空闲 | 改用 `EMBEDDING_PROVIDER=openai` 可降至 1GB 以内 |
| 磁盘 | ≥ 2GB | 依赖 + BGE 模型缓存（bge-small 约 100MB） |

## 启动方法（本地）

### 方式一：一键启动（推荐，Windows）

双击项目根目录的 **`start.bat`**，或在终端执行：

```powershell
.\start.bat
```

脚本 [scripts/start.ps1](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/scripts/start.ps1) 会自动完成 4 件事：

| 步骤 | 行为 |
|---|---|
| 1 | 优先使用 `backend\venv` 的 Python（依赖最完整），没有则用 PATH 里的 `python` |
| 2 | 没有 `backend\.env` 时从 `.env.example` 复制，并提示填写 `DASHCOPE_API_KEY` |
| 3 | 没有 `frontend\dist` 时自动 `npm install` + `npm run build` |
| 4 | **端口预检**：8080 被上次残留的本项目后端占用时，自动结束它再启动；若是其它程序占用，则打印 PID 并退出，不误杀 |

启动成功后输出：

```
用户端: http://localhost:8080/        （示例，实际端口取 backend/.env 的 PORT）
管理端: http://localhost:8080/admin/
按 Ctrl+C 停止服务
```

> 这解决了「每次都报 10048」的问题：脚本只会结束命令行中含 `app.main` 的进程，不会误杀其它程序。

### 方式二：手动启动（五步）

#### 第 1 步：安装后端依赖

```bash
cd backend
python -m venv venv
venv\Scripts\activate                 # Windows
# source venv/bin/activate            # Linux / macOS
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 第 2 步：配置环境变量

```bash
copy .env.example .env                # Windows
# cp .env.example .env                # Linux / macOS
```

打开 `backend/.env`，**只需确认以下三项**即可跑通问答：

```env
# 1) 大模型密钥（二选一，百炼优先）
DASHCOPE_API_KEY=sk-xxx
DASHCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.8-flash

# 2) 本地向量化模型（开箱可用）
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_DEVICE=cpu

# 3) 服务端口
HOST=0.0.0.0
PORT=8080
```

> 完整配置项见上文「配置项速查表」。密钥只写在 `.env`，该文件已被 `.gitignore` 排除。

#### 第 3 步：构建管理端（首次或改动前端后）

```bash
cd frontend
npm install
npm run build                         # 产物 frontend/dist，由后端托管在 /admin/
```

> 跳过此步也可以，但管理端 `/admin/` 会 404；用户端 `/` 是原生 HTML，不依赖构建。

#### 第 4 步：启动后端（一键重启）

```powershell
cd backend
python -m app.main
```

**改完后端代码需要重启服务**。如果重启时提示端口被占用（`WinError 10048`），不用手动查 PID，在项目根目录执行：

```bat
.\restart.bat
```

脚本会自动结束上一次残留的旧后端（仅匹配命令行含 `app.main` 的进程），等待 1 秒后重新启动；也可以直接双击根目录的 `restart.bat`。`start.bat` 已内置同样的端口预检。

> 只改用户端 HTML 不用重启；只改管理端源码则 `npm run build` 后刷新即可；改后端 Python / `.env` / Prompt 才需要重启。

> **默认端口为 8080**，由 [backend/.env](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/.env) 的 `PORT` 统一控制：后端服务、前端 dev 端口与代理目标都读它，无需再改 `vite.config.js`。改 `PORT` 后重启后端即可。
> `npm run dev` 遇到端口已被后端占用时会自动顺延到下一个可用端口（通常 8081），避免冲突。

启动日志出现以下几行即成功：

```
INFO app.db: 数据库初始化完成: sqlite
INFO __main__: 服务启动: <Settings db=sqlite vector=sqlite embedding=local llm=qwen-plus>
INFO: Uvicorn running on http://0.0.0.0:8080
INFO app.services.seed: 初始知识库导入完成：扫描 2，导入 2，跳过 0，失败 0
```

#### 第 5 步：访问两端

> **端口分工（都以 `backend/.env` 的 `PORT` 为准）**
>
> | 服务 | 端口 | 何时使用 |
> |---|---|---|
> | 后端服务（FastAPI） | `PORT`（默认 **8080**） | 始终运行，同时托管用户端与管理端 |
> | 前端开发服务器（Vite） | 从 `PORT` 起步 | 仅 `npm run dev` 开发管理端时 |
>
> 访问 `npm run dev` 的端口若已被后端占用，会自动顺延（8080 → 8081），不再使用固定的 8000。
> 生产模式（`npm run build`）下前端产物由后端托管，**只有 `PORT` 一个端口**，dev 端口不参与。

| 端 | 地址 | 说明 |
|---|---|---|
| 用户端智能客服 | http://localhost:8080/ | 可直接体验问答，也可 iframe 嵌入官网 |
| 管理端后台 | http://localhost:8080/admin/ | 上传文档、查看问答记录 |
| 管理端（开发模式） | http://localhost:8081/admin/ | 端口从 `.env` 的 `PORT` 起步，被后端占用时顺延；接口代理到后端 |
| 健康检查 | http://localhost:8080/api/health | 返回数据库/向量后端/文档数/切片数 |

### 管理端开发模式（可选）

修改 `frontend/` 下的页面时，用热更新开发，不必每次 build：

```bash
cd frontend
npm run dev                           # 端口从 backend/.env 的 PORT 起步（被占用则顺延）
```

Vite 开发端口读取 `backend/.env` 的 `PORT`（默认 8080），启动时会检测该端口是否已有服务监听，被后端占用则自动顺延到下一个可用端口（通常 8081）；`/api` 与 `/user` 代理到后端 `http://127.0.0.1:<PORT>`。开发完成后再执行 `npm run build`，产物由后端在 `PORT/admin/` 下托管。

> 建议**先启动后端、再执行 `npm run dev`**：这样 dev 会自动落到 8081，用户端继续稳定占用 8080，不会互相抢占。

### 国内网络与离线运行

```env
# 首次下载模型走国内镜像（模型未缓存时才真正生效）
HF_ENDPOINT=https://hf-mirror.com

# 模型已缓存后开启离线：不再联网校验，加载不会卡住
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1

# 或资源紧张时换小模型（约 100MB）；换新模型前先把上面两个 OFFLINE 改为 0
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
```

> 以上三项已写入 `.env.example`。**改模型时务必先关掉离线开关**（`HF_HUB_OFFLINE=0`），否则会提示无法下载。

代码层面的双重保障（无需手动设置也能正常工作）：

| 机制 | 说明 | 位置 |
|---|---|---|
| 缓存命中自动离线 | 检测到本地已缓存该模型时，以 `local_files_only=True` 加载，完全不发网络请求 | [embeddings.py](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/app/services/embeddings.py#L23-L31) |
| 启动后台预热 | 服务启动即在后台加载模型，把 20 秒冷启动从"首次提问"挪到"启动阶段" | [main.py](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/app/main.py#L40-L42) |
| 元数据超时收敛 | `HF_HUB_ETAG_TIMEOUT=5`，避免国内网络 5 次重试 × 10 秒 | [config.py](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/app/config.py#L131-L133) |

> 启动阶段报错（`ModuleNotFoundError: app`、`/admin/` 404、问答报「未配置 LLM API Key」、端口占用等）统一见文末 [FAQ · A. 启动与端口](#a-启动与端口)。

### 停止 / 重启服务（改代码后怎么更新）

**`[Errno 10048] ... ('0.0.0.0', 8080)`** 表示 8080 已被占用，几乎都是上一次后端没关。最简单的方式：在项目根目录执行 `.\restart.bat`（自动结束旧后端并启动）。`start.bat` 也内置了端口预检：命中残留的本项目后端会**自动结束**，若是其它程序占用则打印 PID 后退出。

只想停掉后端、不再启动：回到运行它的那个终端按 `Ctrl+C`，直接关掉终端也行。找不到旧终端时才需要 `.\restart.bat`（它会把旧进程结束掉并重新启动）。

#### 修改代码后如何让改动生效

| 改动内容 | 生效方式 |
|---|---|
| 后端 Python（`backend/app/**/*.py`） | **必须重启**（无热重载） |
| 管理端「设置 → 模型服务」（大模型 / 向量化 / Tavily / 检索与分块） | **保存即生效**，无需重启；分块或向量化配置变更时保存会**自动重建全部切片**（失败会提示原因） |
| `backend/.env`（端口、数据库、离线开关等底层默认值） | **必须重启**后端 |
| Prompt（`llm.py` 的 `SYSTEM_PROMPT`） | **必须重启**后端 |
| 用户端 `user/index.html` | 刷新浏览器即可（`Ctrl+F5`），无需重启 |
| 管理端 `frontend/src/**` | 开发时 `npm run dev` 自动热更新；生产需 `npm run build` 后刷新 `/admin/`（无需重启后端） |
| `DOC/` 新增或替换文档 | 重启后端（`SEED_ON_STARTUP=true` 自动导入），或调用 `POST /api/settings/seed` |
| 数据库表结构 | 启动时自动 `create_all` + 轻量 `ALTER`，重启即可 |

**重启最简方式**：在项目根目录执行 `.\restart.bat`（自动结束旧后端并启动），或双击 `restart.bat`；也可以 `Ctrl+C` 停掉后双击 `start.bat`。

> 调试期可用 `python -m uvicorn app.main:app --reload --port 8080`，但每次保存都会重载向量化模型（CPU 约 20 秒）并中断正在输出的回答，仅调试时用；演示/生产用 `python -m app.main`。

## 数据存储与数据库

### 用的是什么数据库

**本地默认 SQLite**（Python 内置、单文件、无需安装数据库服务）。云端可无缝切换为 PostgreSQL（Neon / Supabase 免费层），只需改一个连接串，业务代码不变。

| 数据 | 存放位置 | 说明 |
|---|---|---|
| **业务数据库** | `backend/data/rag.db` | SQLite 单文件，含分类、文档元数据、会话、消息、设置 |
| **向量数据** | 同上 `backend/data/rag.db` 的 `vector_chunks` 表 | `VECTOR_BACKEND=sqlite` 时向量与业务数据同库，便于整体备份 |
| 上传的原始文件 | `backend/data/uploads/` | 按文档 ID 命名保存 |
| Chroma 向量库（可选） | `backend/data/chroma/` | 仅 `VECTOR_BACKEND=chroma` 时使用 |
| 初始知识库 | `DOC/`（项目根目录） | 随仓库提交，启动自动导入 |

`backend/data/rag.db` 中的表：

| 表名 | 内容 |
|---|---|
| `categories` | 知识库分类 |
| `documents` | 文档元数据（文件名、状态、切片数、大小、分类、哈希） |
| `vector_chunks` | 切片文本 + 向量（SQLite 向量后端） |
| `chat_sessions` | 会话（标题、来源 admin/user、归档分类、访客名、时间） |
| `chat_messages` | 问答消息（问题、回答、引用来源 JSON） |
| `qa_categories` | 问答归档分类（名称、颜色、关键词、优先级、启用） |
| `app_settings` | 运行时参数覆盖（如 `top_k`、`web_search_enabled`） |

### 本地数据库在哪配置

全部集中在 [backend/app/config.py](file:///c:/Users/hello/Desktop/AI%20Coding/RAG/backend/app/config.py#L33-L45)：

```python
DATA_DIR   = BASE_DIR / "data"                      # L34，可用环境变量 DATA_DIR 覆盖
DB_PATH    = DATA_DIR / "rag.db"                    # L37
DATABASE_URL = f"sqlite:///{DB_PATH}"               # L45，可被 .env 的 DATABASE_URL 覆盖
```

对应的 `backend/.env` 写法（不写则用上面的默认值）：

```env
# 本地（默认，一般不用写）
DATABASE_URL=sqlite:///C:/Users/hello/Desktop/AI Coding/RAG/backend/data/rag.db

# 云端（换成 Postgres 连接串即可，向量库自动切到 pgvector）
# DATABASE_URL=postgresql+psycopg://user:pass@host/db?sslmode=require

# 可选：把数据目录整体挪到别处（数据库、上传文件、向量库一起搬迁）
# DATA_DIR=D:/rag-data
```

> `sqlite:///` 后面是 **3 个斜杠 + 绝对路径**（Windows 也写正斜杠）。路径含空格时无需转义。

### 查看 / 备份 / 重置数据库

```powershell
# 查看：用 DB Browser for SQLite 打开 backend/data/rag.db，或安装 sqlite3 CLI
sqlite3 backend/data/rag.db ".tables"

# 备份：停止服务后，整体复制 data 目录即可（数据库 + WAL + 上传文件 + 向量）
Copy-Item -Recurse backend\data backend\data_backup_$(Get-Date -Format yyyyMMdd)

# 重置：停止服务 → 删除 rag.db 及其附属文件 → 重启（自动建表并重新导入 DOC）
Remove-Item backend\data\rag.db, backend\data\rag.db-wal, backend\data\rag.db-shm -ErrorAction SilentlyContinue
```

> **备份必须包含 `rag.db-wal` 与 `rag.db-shm`**。本项目 SQLite 使用 WAL 模式，最近的写入先落在 `rag.db-wal` 中；只复制 `rag.db` 会丢失最新数据。最稳妥的做法是**先停止后端再复制整个 `backend/data/` 目录**（正常关闭时 WAL 会自动合并回主文件）。
>
> 同理，重置数据库时三个文件要一起删除，否则可能残留旧数据。

## 云端部署（Vercel + Neon）

前提：代码已推送到 GitHub 仓库，Vercel 与 Neon 账号已就绪。仓库已内置 `api/index.py`、`vercel.json`、`requirements.txt`，无需手动改构建命令。

### 1. 上传代码到 GitHub

```bash
git status
git add backend frontend user api scripts tests vercel.json requirements.txt README.md .gitignore .vercelignore .dockerignore
git commit -m "feat: RAG 双端架构（用户端智能客服 + 管理端文档/记录）"
git push origin main
```

首次初始化仓库时先执行 `git init -b main` 与 `git remote add origin https://github.com/<user>/<repo>.git`，再执行上面三条命令。

| 规则 | 说明 |
|---|---|
| 必须提交 | `backend/`、`frontend/`（含本地 `npm run build` 生成的 `frontend/dist/`）、`user/`、`api/index.py`、`vercel.json`、`requirements.txt` |
| 禁止提交 | `DOC/`（业务文档原件）、`backend/.env`、`backend/data/`、任何密钥；`.gitignore` 与 `.vercelignore` 已排除 |
| 只改前端也要推 | 改动 `frontend/src` 或 `user/index.html` 后先在本地 `cd frontend && npm run build`，再把 `frontend/dist` 一并提交推送；Vercel 构建阶段不保证执行前端构建 |

> 若曾误提交 DOC/ 或密钥：`git push --force` 不能清除 GitHub 上的旧 commit，必须删除仓库重建。

### 2. 在 Vercel 创建 Project 并配置 Neon

1. 在 [Neon](https://neon.tech) 创建数据库，复制连接串并把开头改为 `postgresql+psycopg://`：
   `postgresql+psycopg://USER:PASSWORD@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`
2. [Vercel](https://vercel.com/new) → Import Git Repository → 选择该仓库
3. Framework Preset 选 **Other**，Root Directory 留空（构建与路由由 `vercel.json` 指定）
4. 创建时在 **Environment Variables** 填好 Neon 对应 Key：

| 变量 | 必填 | 说明 |
|---|---|---|
| `DATABASE_URL` | 是 | Neon 连接串（`postgresql+psycopg://...`） |
| `EMBEDDING_PROVIDER` | 否 | 默认 `openai`（Vercel 装不下本地 BGE，勿改回 `local`） |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_MODEL` | 否 | 也可部署后在管理端「设置」填写 |
| `ALLOW_ORIGINS` | 否 | 收敛 CORS 时填线上域名 |

5. Deploy：Vercel 自动执行 `cd frontend && npm install && npm run build`，再把 `api/index.py` 打包为 Python 函数，`/`、`/admin/`、`/api/**` 统一交给 FastAPI
6. 首次启动会自动执行 `CREATE EXTENSION IF NOT EXISTS vector` 并建表，Neon 默认支持 pgvector

> 仓库删除/重建后，Vercel 按仓库 ID 绑定的 Git 连接会失效：需删除旧 Project 重新 Import，否则 `git push` 不会触发部署。

### 3. 后续更新

```bash
git pull origin main   # 同步远端改动
git status
git add backend frontend user api scripts tests vercel.json requirements.txt README.md .gitignore .vercelignore .dockerignore
git commit -m "fix: 说明本次修改"
git push origin main   # Vercel 自动重新部署
```

| 注意点 | 说明 |
|---|---|
| 本机代理 | 本项目已配置 `http.proxy=http://127.0.0.1:7897` + `http.sslBackend=openssl`；换机器后重新执行 `git config http.proxy ...` |
| 历史部署 | Vercel 不会自动删除旧部署，可在 Deployments 里清理 |
| DOC/ 与密钥 | 永远不进 `git add`；云端知识库在管理端「文档管理」上传 |

### 4. 首次访问与初始化

| 端 | 地址 |
|---|---|
| 用户端智能客服 | `https://<project>.vercel.app/` |
| 管理端后台 | `https://<project>.vercel.app/admin/` |
| 健康检查 | `https://<project>.vercel.app/api/health` |

部署自检：`/api/health` 返回 200 JSON；`/` 与 `/admin/` 正常渲染；上传文档后提问有回答，点击引用 `[n]` 能打开右侧原文并高亮。

若 `/admin/` 返回 404：检查 Build Logs 是否执行了 `npm run build`、`vercel.json` 是否在仓库根、Root Directory 是否为空。
若 `/api/health` 报数据库错误：检查 `DATABASE_URL` 是否为 `postgresql+psycopg://` 开头，且使用 Neon 的 pooled 连接串。

首次进入管理端 →「设置」填写 Key（默认均为空，保存即写入数据库，无需重新部署）：

| 配置 | 位置 | 说明 |
|---|---|---|
| 大模型 API Key / Base URL / 模型名 | 设置 → 模型服务 → 大模型 | 不填则问答提示缺少 Key |
| 向量化 Key（可留空复用大模型 Key） | 设置 → 模型服务 → 向量化 | openai 兼容 Embedding API |
| Tavily Key（可选） | 设置 → 模型服务 → 联网搜索 | 不填则跳过联网 |

首次使用在「文档管理」上传文档，向量写入 pgvector 后即可问答（`DOC/` 未打包进 Serverless 函数，「导入初始文档」在线上不可用）。

> **Serverless 限制**
> - 文件系统只读，数据目录自动落到 `/tmp/rag-data`：**业务库与向量必须走 Neon（`DATABASE_URL`）**，否则冷启动后数据丢失。
> - 上传原文件只存在于 `/tmp`，实例回收即丢失；但入库时已把原文渲染成 HTML 存入 `documents.preview_html`，右侧预览与引用高亮不受影响；「重建向量索引」仍需原文件。
> - 函数最长执行 60 秒（`vercel.json` 已设 `maxDuration: 60`），冷启动有延迟。
> - 不支持本地 BGE / ChromaDB，向量化固定用 OpenAI 兼容 API。

## 核心 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（数据库/向量后端/文档数/切片数） |
| POST | `/api/documents/upload` | 上传文档（multipart，可带 `category_id`） |
| GET | `/api/documents` | 文档列表（`category_id`/`status`/`keyword`） |
| GET | `/api/documents/stats` | 统计（数量/切片/存储/类型分布/分类分布） |
| GET | `/api/documents/{id}/chunks` | 切片预览 |
| GET | `/api/documents/{id}/file` | 下载文档原文件（PDF 预览也走此接口） |
| PATCH | `/api/documents/{id}` | 修改文件名/分类 |
| DELETE | `/api/documents/{id}` | 删除文档与向量 |
| GET | `/doc/{doc_id}?chunk=N&span=S&span_len=L&embed=1` | 文档预览页：按原文件格式渲染整篇文档（Word / PPT / Markdown / TXT / PDF），滚动并精准高亮第 N 个切片中偏移 S、长度 L 的引用句子（缺省回退整段锚点）；`embed=1` 为问答页右侧内嵌的紧凑模式 |
| GET/POST | `/api/categories` | 分类列表 / 新建 |
| PATCH/DELETE | `/api/categories/{id}` | 分类修改 / 删除 |
| GET | `/api/knowledge/overview` | 知识库分类视图 + 总览统计 |
| POST | `/api/public/chat` | **用户端**问答（SSE，会话自动标记 `source=user`） |
| POST | `/api/chat` | 管理端问答（SSE，会话标记 `source=admin`） |
| GET | `/api/sessions` | 会话列表（`?source=user/admin` 筛选） |
| GET | `/api/sessions/stats` | 会话统计（总会话/用户端/管理端/消息数） |
| GET | `/api/sessions/{id}/messages` | 会话历史 |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| GET/PUT | `/api/settings` | 运行时设置读取 / 更新（检索分块参数 + 联网搜索开关） |
| GET/POST | `/api/qa-categories` | 问答归档分类列表 / 新增 |
| PUT/DELETE | `/api/qa-categories/{id}` | 问答归档分类修改 / 删除（「其他」不可删） |
| POST | `/api/settings/seed` | 手动导入 `DOC/` 初始文档 |

**前端访问路径**：用户端 `http://127.0.0.1:8080/`，管理端 `http://127.0.0.1:8080/admin/`。

## RAG 实际执行流程

通用教程常把 RAG 描述为 5 步（加载→切分→向量化入库→建 Prompt→初始化 LLM），但本项目因为**上传入库**和**问答检索**是两条独立链路，实际执行步骤拆得更细。下面按"开发者视角"逐步说明每一步做了什么、对应哪个文件的哪个函数。

### 链路 A：文档入库（上传 / 启动自动导入）

**触发入口**（谁发起调用）：
- 前端上传：`backend/app/routers/documents.py:37` `upload_document()`
- 启动自动导入 / 手动种子：`backend/app/services/seed.py:19` `seed_documents()` 或 `backend/app/routers/settings.py:38` `import_seed_documents()`
- 核心流水线：`backend/app/services/ingest.py:44` `ingest()`

| 步骤 | 做了什么 | 具体代码（`backend/` 相对路径） |
|---|---|---|
| A1 接收文件 + 哈希 | 计算 SHA-256 文件摘要 | `app/services/ingest.py:29` `file_sha256()` |
| A2 去重判断 | 查 DB 中是否已有同 hash 文档；已有且 `ready` → 跳过；已有但 `failed` → 清旧数据重试 | `app/crud.py:181` `find_document_by_hash()`<br>`app/services/ingest.py:76` 调用向量库删除旧切片<br>`app/services/vector/sqlite_store.py:75` `delete_by_doc()`（本地）或 `app/services/vector/pgvector_store.py:113`（云端） |
| A3 自动分类 | 文件名关键词匹配 → 归入对应分类 | `app/services/ingest.py:33` `infer_category_id()` → `app/crud.py:65` `get_or_create_category()` |
| A4 落盘 + 建 DB 记录 | 文件写入 `backend/data/uploads/`，DB 插入 `documents` 表，状态先标记 `processing` | `app/services/ingest.py:80` 落盘在同函数内<br>`app/crud.py:121` `insert_document()` |
| A5 解析抽取 | 按扩展名分派解析器。WPS 非法 docx 自动回退 OOXML | `app/services/parser.py:134` `extract_and_clean()`（统一入口）<br>内部按扩展名分派：<br>· docx：`parser.py:56` `_extract_docx()` → 异常回退 `parser.py:73` `_extract_docx_ooxml()`<br>· pdf：`parser.py:36` `_extract_pdf()`<br>· pptx：`parser.py:92` `_extract_pptx()`<br>· md/txt：`parser.py:109` `_extract_text()` |
| A6 清洗 | 去除空白、过短片段（<10 字符）、全特殊字符 | `app/services/parser.py:123` `clean_text()`（被 `extract_and_clean()` 在 L139 调用） |
| A7 中文分块 | 分隔符优先级 `\n\n → \n → 。！？ → ；， → 空格`，默认 600 字符 / 120 重叠 | `app/services/chunker.py:30` `chunk_document()` → 内部用 `chunker.py:19` `_build_splitter()` 构建 `RecursiveCharacterTextSplitter` |
| A8 向量化 | 每个切片生成 512 维向量，首次调用时懒加载 BGE 模型并缓存 | `app/services/embeddings.py:150` `get_embedder()`（懒加载入口）<br>`app/services/embeddings.py:161` `embed_documents()`（批量编码，被 vector_store 在 `add_chunks()` 内部调用）<br>本地 BGE 实现：`app/services/embeddings.py:44` `LocalBGEEmbedder` |
| A9 写入向量库 | 连同元数据（`doc_id` / `filename` / `chunk_index`）批量写入 | `app/services/vector/__init__.py:15` `get_vector_store()`（工厂）<br>`app/services/vector/sqlite_store.py:49` `add_chunks()`（本地 SQLite 默认）<br>或 `app/services/vector/pgvector_store.py:85`（云端 Postgres + pgvector）<br>或 `app/services/vector/chroma_store.py:28`（可选 ChromaDB） |
| A10 更新状态 + 回滚 | A5-A9 任何一步异常 → 回滚向量切片 + 标记 DB 为 `failed`；成功 → 置 `ready` | `app/crud.py:147` `update_document()`（更新状态）<br>`app/services/ingest.py:116-123` `except` 块内的回滚逻辑 |

### 链路 B：问答（前端对话框）

**触发入口**：用户端 `backend/app/routers/chat.py:38` `public_chat()`（`POST /api/public/chat`）；管理端 `chat.py:32` `chat()`（`POST /api/chat`）。两者共用 `_do_chat()`，核心函数 `backend/app/services/rag.py:50` `stream_answer()`。

| 步骤 | 做了什么 | 具体代码（`backend/` 相对路径） |
|---|---|---|
| B1 读取运行时配置 | 读进程内运行时配置（启动时由 `app_settings` 覆盖 `.env`，保存设置后即时刷新） | `app/routers/chat.py:50` `settings_service.get_runtime()` |
| B2 问题向量化 | 用户问题走 BGE 编码为 512 维 | `app/services/vector/sqlite_store.py:80` `search()` 内部在 L83 调用 `app/services/embeddings.py:165` `embed_query()` |
| B3 向量召回 TopK | 余弦相似度搜索，过滤低于阈值的结果 | 同上 `app/services/vector/sqlite_store.py:80` `search()`（L84 归一化 + L97 点积计算余弦相似度 + L99 阈值过滤 + L111-L113 TopK 截取）<br>如果配置了 pgvector 则自动路由到 `pgvector_store.py:120` |
| B4 格式化上下文 + 绑定引用 | 召回切片拼成 `[1]（文件名）\n文本\n\n[2]...` 格式，同时整理 sources 列表；编号按检索顺序统一（检索 = 上下文 = LLM 看到的 [n] = 前端卡片），并按问题关键词从切片中挑出细粒度原文句子作为 `span_start/span_len/span_text` | `app/services/rag.py:17` `build_context()` → `app/services/span.py` `locate_span()` |
| B5 构建 Prompt | 系统提示词（`SYSTEM_PROMPT`）+ 用户问题 + 格式化后的参考资料 → 组装两条 message | `app/services/llm.py:46-47`（system / user message 拼接） |
| B6 获取模型客户端 | 按运行时配置（设置页或 `.env`）的 Key / Base URL 创建客户端；模型名取配置中的 `llm_model` | `app/services/llm.py:33` `get_client()` |
| B7 流式生成 | OpenAI 兼容 SDK 的 `chat.completions.create(stream=True)` 逐 token yield | `app/services/llm.py:43-55`（API 调用与 token 迭代） |
| B8 SSE 推送 | 先推 `sources`，再推若干 `token`，最后 `done` 或 `error` | `app/services/rag.py` `stream_answer()`：L64 推 sources / L71 无命中兜底后 done / L78 推 token / L79 推 done / L81、L84 推 error |
| B9 保存会话 | SSE 流结束后落库 `chat_sessions` + `chat_messages` 表，并标记来源（`admin`/`user`） | `app/routers/chat.py:50` `crud.create_session()`（若未传 session_id）<br>`app/routers/chat.py:53` 用户问题落库<br>`app/routers/chat.py:72` assistant 回答 + sources 落库 |

### 与通用 RAG 教程的差异

| 通用教程 | 本项目实际做法 | 原因 |
|---|---|---|
| "加载文档 + 切分" 一步 | 拆成 A5 解析 → A6 清洗 → A7 分块三步 | 不同格式解析逻辑不同；清洗避免把空白/乱码送进向量库 |
| "初始化 LLM" 在服务启动时 | LLM 客户端在每次问答时按需创建（模型/厂商由运行时配置决定） | 客户端创建开销极小；切换模型保存即生效，不需要常驻状态 |
| 向量化和入库是同一步 | 向量化（embeddings）独立成 `services/embeddings.py` | 本地 BGE / API 向量化可切换 |
| 问答是黑盒 | 先检索 → 先推 sources → 再流式推 token | 前端需要先渲染引用卡片，再让回答渐进出现 |
| "构建提示词" 在生成模型类里 | 系统提示词硬编码在 `llm.py` 的 `SYSTEM_PROMPT` | 反幻觉：要求标注 `[n]` 引用、无依据时明确说明 |
| 没有错误处理 | 入库链路有完整的 try/except 回滚，问答链路对 429/密钥未配置等有分类错误提示 | 企业级系统不能容忍脏数据和静默失败 |

## 关键实现说明

- **存储自适应**：`config.Settings.effective_vector_backend` 按 `DATABASE_URL` 自动选择 `pgvector`（Postgres）或内置 `sqlite` 向量表；也可用 `VECTOR_BACKEND` 强制指定。
- **向量库抽象**：`services/vector/` 下三种实现共享 `add_chunks / delete_by_doc / search / get_chunks / count` 接口，业务层无感知。
- **入库一致性**：解析或向量化失败时，文档标记 `failed` 并回滚已写入切片；服务重启会将残留 `processing` 标记为 `failed`。
- **重复文件去重**：按文件 SHA-256 判定，重复上传直接跳过；失败记录重传会先清理旧数据再重建。
- **DOCX 兼容**：部分 WPS 导出的 docx 含非法 media part，`python-docx` 解析失败时自动回退直接读取 `word/document.xml`。
- **中文分块**：分隔符优先级 `\n\n → \n → 。！？ → ；， → 空格`，默认 600 字符 / 120 重叠。
- **反幻觉**：系统提示词要求仅依据参考资料回答、标注 `[n]` 引用、无依据时明确说明。

## 测试

```bash
# 先在 backend/ 启动服务，再在项目根目录运行
python tests/smoke_test.py
```

覆盖：健康检查、分类增删、知识库总览、设置读写、文档上传、切片预览、检索问答、会话列表、改分类、初始导入、删除文档。

> 离线联调可用 `EMBEDDING_PROVIDER=hash`（确定性哈希向量，无需下载模型），真实检索效果请使用 BGE 或云端 API。

## 常见问题与排查（FAQ）

> 全部按「现象 → 原因 → 解决」组织。先查下面的快速索引定位小节，再按表内的指引操作；表格里涉及的文件改动，改完都需要重启后端（详见 [修改代码后如何让改动生效](#修改代码后如何让改动生效)）。

### 快速索引

| 你遇到的现象 | 对应小节 |
|---|---|
| 启动报错、端口占用、`/admin/` 404、`ModuleNotFoundError: app` | [A. 启动与端口](#a-启动与端口) |
| 改了 `.env` / Prompt / 分块参数，回答却毫无变化 | [B. 配置改了却没生效](#b-配置改了却没生效) |
| 回答很慢：转圈久、首字要等好几秒、整段输出拖沓 | [C. 回答慢：从哪慢、怎么快](#c-回答慢从哪慢怎么快) |
| 总是「未查询到相关内容」/ 寒暄也命中无关内容 | [D. 检索与阈值](#d-检索与阈值) |
| 上传失败、切片数为 0、重复上传没反应 | [E. 文档入库](#e-文档入库) |
| 管理端看不到问答记录 / 自动分类不准 | [F. 问答记录与归档](#f-问答记录与归档) |
| 联网开关打开了，来源里却没有网页 | [G. 联网搜索（Tavily）](#g-联网搜索tavily) |
| 备份恢复后数据变少、想清库重建、要上云 | [H. 数据与迁移](#h-数据与迁移) |
| iframe 嵌入官网、跨域被拦、管理端鉴权 | [I. 嵌入官网与安全](#i-嵌入官网与安全) |
| 想看历史踩坑的根因与修复细节（含加载慢、版本冲突） | [J. 历史问题根因汇总](#j-历史问题根因汇总) |

### A. 启动与端口

| 问题 | 排查与解决 |
|---|---|
| 启动报 `[Errno 10048]` / 端口被占用 | 几乎都是上次的后端没退出。用 `.\restart.bat` 自动结束旧后端并重启；手动方式见 [停止 / 重启服务](#停止--重启服务改代码后怎么更新) |
| 不确定端口被谁占用 | 直接跑 `.\restart.bat`，它会自动结束旧后端；只想看是谁占用，用 `netstat -ano \| findstr :8080` 查 PID |
| `ModuleNotFoundError: app` | 启动目录不对，必须在 `backend/` 下执行 `python -m app.main` |
| `/admin/` 返回 404 | 管理端未构建：`cd frontend` → `npm run build`，然后刷新 `/admin/` |
| 前端 `npm run dev` 端口与预期不同 | 开发端口从 `.env` 的 `PORT` 起步，8080 被后端占用时会自动顺延（通常 8081）；以终端输出的实际地址为准 |
| 页面上是旧界面 / 改动不生效 | 后端代码与 `.env` 需重启；管理端需重新 build；浏览器 `Ctrl+F5` 清缓存 |

**端口约定**：端口统一由 `backend/.env` 的 `PORT` 决定（默认 **8080**）。后端始终监听该端口并同时托管两端；`npm run dev` 从同一端口起步，被占用时自动顺延（通常 8081）。遇到 10048 **不要反复重试**，在项目根目录执行 `.\restart.bat` 即可自动结束旧后端并重启。

### B. 配置改了却没生效

先记住一条优先级：**管理端保存的 `app_settings` 值 > `backend/.env` 默认值**。模型/检索/Key 类配置在「设置 → 模型服务」改完保存即生效；`.env` 只在启动时读取。

| 现象 | 原因 | 解决 |
|---|---|---|
| 管理端保存后看起来没变化 | 设置了但没点「保存」；或浏览器缓存旧页面 | 确认点击保存并出现「已保存，立即生效」；`Ctrl+F5` 刷新 |
| 切换向量模型后检索不到内容 | 旧切片向量与新模型不在同一向量空间 | 保存后点「立即重建向量索引」，或删除文档重新上传 |
| 改 `CHUNK_SIZE` 后已有文档没变化 | 分块只在入库时执行 | 在设置页改分块大小并保存（自动重建全部切片），或点「立即重建向量索引」，或删除文档后重新上传 |
| 改 `.env` 的检索参数无效 | 管理端已保存过同名配置 | 在「模型服务」里改，或清空 `app_settings` 中对应该项 |
| 换模型后报 401 / 404 | Base URL 与厂商不匹配 | 使用设置页的快捷填充（百炼 / DeepSeek / 硅基流动），或核对厂商接口地址 |
| 密钥框里已有内容 / 为空 | 设置页会回显当前生效的 Key（管理端保存值优先，其次 `.env`）；两者都没有时为空 | 要换 Key 直接改成新值；**清空后保存即删除该 Key**；未改动的密钥不会被重置 |
| 改 Prompt 后回答没变化 | ① 问题未命中知识库，走了兜底文案 ② 后端未重启 | 用能命中的问题验证（出现「📎 参考来源」才调用了 LLM）；重启后端 |
| 改了管理端页面没变化 | 后端托管的是 `frontend/dist` 构建产物 | `npm run build` 后刷新；开发期用 `npm run dev` |

### C. 回答慢：从哪慢、怎么快

一次提问要经过三段，**先看时间花在哪一段**，再对症下药：

| 阶段 | 观察点 | 典型耗时 | 主要影响因素 |
|---|---|---|---|
| ① 首包（来源就绪） | 点「发送」到出现「📎 参考来源」 | 0.6~1.5 秒 | 向量化：本地模型冷启动 / 在线 Embedding 的网络往返 |
| ② 首字（TTFT） | 来源出现到开始逐字输出 | 0.5~2 秒 | `LLM_MODEL` 选型、上下文总长度（`TOP_K` × 切片字数） |
| ③ 整体输出 | 整段回答的总时长 | 与回答字数成正比 | 模型生成速度、回答长度、Prompt 要求的篇幅 |

**按优先级依次调整**（每项改完都要重启后端）：

| # | 措施 | 怎么改 | 效果 |
|---|---|---|---|
| 1 | **换成快模型** | `LLM_MODEL=qwen-plus`（或更小的 `qwen-turbo`） | 最见效。`qwen-plus` 首字约 0.5 秒，超大模型（如 `qwen3.8-2.4t-a95b`）首字 1.9~2 秒以上 |
| 2 | **本地向量化改在线** | `EMBEDDING_PROVIDER=openai` + 百炼 Embedding，见 [本地向量化改在线](#本地向量化改在线无需本地模型启动更快) | 免掉本地模型 20~30 秒冷启动，首包更快；**换模型后必须重建索引**，否则检索全部不匹配 |
| 3 | **提高相似度阈值** | `SCORE_THRESHOLD=0.35` → `0.45` | 过滤掉弱相关切片，喂给模型的上下文更短 → 首字更快、答案更聚焦；若因此频繁命中「未查询到」，再回调到 `0.35~0.4` |
| 4 | **减少召回条数** | `TOP_K=5` → `3` | 每少一条切片就少几百字上下文，首字延迟明显下降 |
| 5 | **精简提示词** | 改 `backend/app/services/llm.py` 的 `SYSTEM_PROMPT` | Prompt 越短、要求的分点数越少，首字与总时长都下降（示例见下） |
| 6 | **本地模式保持离线** | `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` | 仅 `EMBEDDING_PROVIDER=local` 有效，避免联网校验拖到 50 秒 |
| 7 | **排查网络出口** | 在线模型 / Embedding 需访问外网 | 代理或线路抖动会让首字翻倍，看后端日志中首个 chunk 的时间点 |

**提示词精简示例**（原版 5 条要求，可压到 3 条，格式稳定性基本不变）：

```python
SYSTEM_PROMPT = """只能依据下面上下文回答，禁止编造。
参考上下文每条以 [编号] 开头，该编号即引用编号：
{context}
用户问题：{question}
回答要求：
1. 纯文本作答，不要使用 Markdown 标记
2. 先一句话给结论，再按「1. 2. 3.」分点说明
3. 在对应句末标注 [编号]；上下文没有答案时只回复：【知识库中未查询到相关内容】"""
```

> 阈值与 `TOP_K` 是「速度↔召回」的杠杆，不是越大越好：`SCORE_THRESHOLD` 从 0.35 提到 0.45、`TOP_K` 从 5 降到 3 后，上下文条数从 5 条降到 3 条，首字更快，但可能漏掉部分边缘相关的内容；如果发现答案开始兜底，就先回调阈值，再动 `TOP_K`。

**实测参考**（`qwen-plus` + 在线 Embedding、`TOP_K=5`）：首包 0.67 秒、首字 1.54 秒；相比本地 BGE + 超大模型（首包 50 秒以上、首字 2 秒以上）差别主要在「冷启动」和「模型选型」两处。

> 本项目未内置检索缓存。若要进一步压低首字延迟，可考虑同一问题的向量结果缓存，或把历史多轮对话裁剪到最近 1~2 轮。

### D. 检索与阈值

| 现象 | 原因 | 解决 |
|---|---|---|
| 寒暄（如「你是谁」）返回无关答案 | `SCORE_THRESHOLD` 设成了 `0`，等于不过滤任何切片 | 改回 `0.35~0.45` 并重启，未命中时会走兜底文案 |
| 回答总是「知识库中未查询到相关内容」 | 检索结果低于阈值，或知识库确实没有 | 先把 `SCORE_THRESHOLD` 调低（如 `0.2`）验证是否为阈值问题；再确认文档状态「已就绪」、补充文档 |
| 参考来源条数太少 / 太多 | 由 `TOP_K` 控制 | 改 `.env` 的 `TOP_K` 并重启 |
| 回答太发散 / 太死板 | 由 `LLM_TEMPERATURE` 控制 | 稳定取 `0.1~0.3`，需要发散取 `0.6~0.8` |
| 报「未配置 LLM API Key」 | `.env` 中 `DASHCOPE_API_KEY` 与 `DEEPSEEK_API_KEY` 都为空 | 至少填一个并重启；注意变量名是 `DASHCOPE_`（项目沿用的拼写） |
| 首次提问转圈很久 / 日志刷 `huggingface timed out` | 本地 BGE 模型加载时联网校验超时 | 设 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`；代码已做缓存检测 + 启动预热 |
| 换 Embedding 模型后检索全乱 | 新旧向量维度 / 空间不一致 | 删除所有文档重新导入（`EMBEDDING_PROVIDER=hash` 仅用于离线联调） |

### E. 文档入库

| 现象 | 排查与解决 |
|---|---|
| 上传后状态 `failed` / 切片数为 0 | 打开文档列表查看失败原因；常见为扫描版 PDF 无文本、编码异常、清洗后内容过短；处理后重新上传 |
| 同一文件重复上传没有新增 | 按 SHA-256 去重，已有且 `ready` 会直接跳过；需要重导先删除旧文档 |
| WPS 导出的 docx 解析失败 | 代码已自动回退 OOXML 解析；若仍失败，用 Word 另存为标准 `.docx` 再传 |
| 支持哪些格式 | `.pdf` / `.docx` / `.pptx` / `.txt` / `.md`，单文件上限由 `MAX_FILE_SIZE_MB` 控制（默认 50MB） |
| 大文件处理慢 | 上传为同步处理；>20MB 扫描版 PDF 建议先 OCR（系统未内置） |

### F. 问答记录与归档

| 现象 | 排查与解决 |
|---|---|
| 用户端提问后管理端看不到 | 记录已入库（`chat_sessions.source=user`）；问答记录页每 10 秒自动刷新，也可点「刷新」；确认后端 `/api/sessions` 能返回数据 |
| 会话列表为空但接口有数据 | 前端调用错误或用了旧构建，重新 `npm run build` 并 `Ctrl+F5` |
| 统计数字与列表不一致 | 统计是数据库全量计数，列表默认只取最近 200 条 |
| 分类全部显示「其他」 | 问题未命中任何关键词；在管理端「设置 → 问答分类」补充业务关键词后保存即可生效（历史会话需点「重置」或清空 `category` 才会重新归档） |
| 改了分类但新问题仍归旧类 | 分类保存后 15 秒规则缓存自动刷新；确认该分类是「启用」状态且顺序（优先级）合适 |

### G. 联网搜索（Tavily）

| 现象 | 排查与解决 |
|---|---|
| 打开联网开关但来源里没有网页 | 检查 `backend/.env` 的 `TAVILY_API_KEY` 是否填写（管理端「设置 → 系统信息」显示 `Tavily Key` 是否已配置）；未配置时后端会记录「未配置 TAVILY_API_KEY，联网搜索已跳过」告警 |
| 刚填了 Key 仍不生效 | Key 属于环境变量，必须**重启后端**；开关与时机属于运行时设置，保存即生效 |
| 联网条目太多 / 太少 | 「返回网页条数」限制 1~10，默认 5 |
| 每次提问都联网导致额度消耗快 | 搜索时机改为「仅本地知识库未命中时联网」（`fallback`），命中知识库就不联网 |
| 联网请求失败会影响回答吗 | 不会。搜索超时 15 秒且异常被捕获，失败时降级为仅用本地知识库，问答不中断 |
| 提示 401 / 432 等错误 | Tavily Key 无效或额度用尽，登录 tavily.com 检查账户与用量 |

### H. 数据与迁移

| 现象 | 排查与解决 |
|---|---|
| 不知道数据存在哪 | 本地全部在 `backend/data/`（`rag.db` 含业务与向量、`uploads/` 原文件），详见 [数据存储与数据库](#数据存储与数据库) |
| 备份后恢复数据变少 | SQLite 开了 WAL，最新写入在 `rag.db-wal`；停服务后复制整个 `backend/data/`，不要只拷 `rag.db` |
| 想清空重建 | 停服务 → 删除 `rag.db`、`rag.db-wal`、`rag.db-shm` → 重启（自动建表并重新导入 `DOC/`） |
| 上云怎么切 | 改 `DATABASE_URL` 为 Postgres 连接串，`VECTOR_BACKEND=pgvector`，`EMBEDDING_PROVIDER=openai`，详见上文「云端部署（Vercel + Neon）」 |

### I. 嵌入官网与安全

| 事项 | 做法 |
|---|---|
| iframe 嵌入官网 | `<iframe src="http://your-domain/" width="380" height="600" style="border:0;border-radius:12px"></iframe>` |
| 嵌入后跨域被拦 | `.env` 的 `ALLOW_ORIGINS` 要包含官网域名（生产不要保留 `*`） |
| 管理端没有登录 | 本项目未内置用户体系，`/admin/` 建议在网关层加鉴权 |
| Windows 终端中文乱码 | `chcp 65001`，或使用 UTF-8 终端查看日志 |
| 密钥安全 | 生产环境优先在管理端「设置 → 模型服务」填写 Key（存数据库，打开设置页会回显当前值）；`.env` 中的 Key 已被 `.gitignore` 排除，不要把 `.env` 提交到 Git |

### J. 历史问题根因汇总

> 以下均为本项目实际遇到并已修复的问题，按「症状 → 根因 → 解决」记录，便于在新项目里提前规避。

| # | 症状 | 根因 | 解决（已落地） |
|---|---|---|---|
| 1 | 用户端首次提问转圈 50~70 秒；日志反复 `Retrying [Retry n/5]`，URL 为 `huggingface.co/.../modules.json` | ① 加载时联网做 HEAD 校验（10s 超时 × 5 次）② BGE 冷启动约 20s ③ 模型在首次提问时才懒加载 | 离线开关 + 本地缓存检测 + 启动预热 + 超时收敛（详见下文「加载时间过长」） |
| 2 | 启动报 `Unrecognized processing class in BAAI/bge-small-zh-v1.5` | transformers 5.x 与 BGE v1.5 tokenizer 不兼容；sentence-transformers 6.x 又强制要求 transformers≥5 | 锁版本：`transformers>=4.41,<5` + `sentence-transformers>=3.0,<4`（详见下文「BGE 版本冲突」） |
| 3 | 启动报 `[Errno 10048] ... ('0.0.0.0', 8080)`，每次都冲突 | 上一次后端进程未退出（多终端 / IDE 后台任务残留） | 启动前端口预检并输出中文指引；前端 dev 端口跟随 `.env` 的 `PORT`（被占用时顺延）；处理方式见 [A. 启动与端口](#a-启动与端口) |
| 4 | ChromaDB 在 Python 3.13/3.14 报 `RustBindingsAPI` 错误 | chromadb 轮子尚未适配新版 Python | 本地默认改用**内置 SQLite 向量表**（`vector_chunks`），chromadb 降级为可选后端 |
| 5 | WPS 导出的 `.docx` 上传即失败 | zip 内含非法 media/关系项，python-docx 拒绝解析 | `parser.py` 捕获异常后回退为直接解析 OOXML 的 `word/document.xml` |
| 6 | 重新导入文档后仍检索到旧内容 | 失败记录重建时未清理旧向量 | `ingest.py` 重建前先 `store.delete_by_doc(doc_id)`；整条链路失败回滚 |
| 7 | 改了 `.env` 的 `SCORE_THRESHOLD` / `TOP_K` 却不生效 | `settings_service.effective()` 用 `app_settings` 表覆盖 `.env` | 清空数据库残留配置，明确「数据库 > .env」优先级（本项目现以 `.env` 为唯一来源） |
| 8 | 改了 Prompt，回答没变化 | ① 问题未命中知识库 → 走 `rag.py` 写死的兜底文案 ② 后端未重启（`SYSTEM_PROMPT` 是模块常量） | 用能命中的问题验证（有「📎 参考来源」卡片才调了 LLM）；改后重启；Prompt 只保留一处 |
| 9 | 备份数据库后恢复发现数据变少 | SQLite 开启 WAL，最新写入在 `rag.db-wal` | 停服务后复制整个 `backend/data/`，勿只拷 `rag.db` |
| 10 | 用户端提问了，管理端看不到记录 | 记录已入库，管理端页面只加载一次 | 问答记录页改为每 10 秒自动刷新 + 手动刷新按钮 |
| 11 | 上传后文档 0 切片 / 状态 `failed` | 清洗后文本过短被过滤、类型不支持、编码异常 | 看文档失败原因后重传；支持 `.pdf/.docx/.txt/.md/.pptx` |
| 12 | 问答记录页始终「暂无会话记录」，但接口能返回数据 | 前端把 `api` 对象当函数调用（`api(url)`），抛 `TypeError` 被静默吞掉 | 统一走 `api.listSessions()` / `api.sessionStats()` / `api.sessionMessages()`，并重新构建前端 |
| 13 | 参考来源点开后是一屏切片文字，不像原文档 | 早期定位页直接拼接 `vector_chunks` 的切片文本 | 改为按原文件类型渲染：docx 走 mammoth（保留标题/表格/图片）、pptx 按页显示、Markdown 渲染语法（标题/列表/表格/代码块）、txt 按空行分段并保留原换行、pdf 用 iframe `#page=N` |
| 13.1 | 点击 `[n]` 后高亮跑到了无关的目录/标题行 | 只按切片前 12 个字做节点级模糊匹配，切片开头常是目录或上一段残留 | 切片入库时保存 `char_start/char_end` 字符偏移；召回后 `span.py` 按问题关键词从切片里挑出最相关的一两句话（`span_start/span_len`）；预览页把片段写入 `data-quote`，JS 用「去空白全文索引 + Range」逐字符定位并包裹 `.hit`，匹配失败才回退 `data-anchor` 粗定位。旧切片无需重切也能获得 span 高亮 |
| 14 | 联网开关打开后仍回复「未查询到相关内容」 | 页面拨动开关后未点「保存」，`app_settings.web_search_enabled` 仍为 `false` | 保存即写入数据库并立即生效；`.env` 中的 `TAVILY_API_KEY` 变更才需要重启 |

#### 加载时间过长是怎么解决的（重点）

**问题链路**：`SentenceTransformer(...)` 加载时 → 向 `huggingface.co` 请求 `modules.json` 做元数据校验 → 国内网络 10 秒超时 → 自动重试 5 次 ≈ 50 秒 → 之后才开始真正加载模型（CPU 下约 20 秒）。而模型是「首次提问时才懒加载」，用户把这 70 秒全等在了对话框上。

**四层解决措施**（对应文件）：

| 措施 | 位置 | 说明 |
|---|---|---|
| 离线开关 | `backend/.env` 的 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` | 模型已缓存时完全不发起网络请求 |
| 本地缓存检测 | `backend/app/services/embeddings.py` | 检测 HF 缓存目录存在则用 `local_files_only=True` 加载 |
| 启动预热 | `backend/app/main.py` | 启动后用后台线程 `warmup()` 提前加载模型，把 20s 挪到启动阶段 |
| 超时收敛 | `backend/app/config.py` | `HF_HUB_ETAG_TIMEOUT=5`，即使在线也不会被重试拖住几十秒 |

**实测效果**：首个 SSE 数据包（TTFB）从 50 秒以上降到 **0.012 秒**，来源卡片几乎瞬时出现，之后是模型逐字生成时间。

**注意**：首次部署仍需联网下载一次模型（约 100MB）；下载完成后再把离线开关设为 `1`。换新模型时要临时改回 `0`。

#### BGE 模型版本冲突

```powershell
# 用运行服务的那一个 python 执行，避免多环境装错位置
python -m pip install "transformers>=4.41,<5" "sentence-transformers>=3.0,<4"
python -c "import transformers, sentence_transformers as st; print(transformers.__version__, st.__version__)"
```

若出现「独立脚本能加载、服务进程不能加载」，先确认两者是同一个 Python：`Get-Command python` 与启动服务的解释器路径是否一致。
