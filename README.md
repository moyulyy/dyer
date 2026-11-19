# dyer · 抖音数据采集

基于 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 抖音引擎的 **Web 版数据采集工具**：
在浏览器里创建采集任务、实时查看运行日志、扫码登录、浏览/检索采集结果并导出。

> **免责声明**：本项目及内置引擎仅供**学习与研究**使用。请遵守目标平台的服务条款、robots 协议与相关法律法规，
> 不得用于商业用途、大规模爬取或任何干扰平台正常运营的行为。使用本项目产生的一切后果由使用者自行承担。
>
> 内置引擎 `engine/` 源自 MediaCrawler，遵循其 **NON-COMMERCIAL LEARNING LICENSE 1.1**（见 `engine/LICENSE`），
> 因此本仓库整体在涉及该引擎的部分同样**不得用于商业用途**。

## 功能特性

- **四种采集模式**：关键词搜索、指定作品（链接 / ID / 短链）、创作者主页、社交网络扩散
- **登录方式**：扫码登录（二维码直接显示在网页里）、Cookie 登录（可保存复用）
- **实时日志**：WebSocket 推送引擎日志，按级别着色，任务结束自动汇总
- **任务管理**：创建 / 排队 / 停止 / 重试 / 删除，进程隔离，服务重启后自动清理残留状态
- **数据浏览**：作品列表筛选、排序、分页，作品详情与评论查看
- **数据导出**：CSV / JSON，导出内容与当前筛选条件一致
- **数据概览**：作品/评论/点赞统计、关键词分布、每日作品量、高赞作品榜
- **用户维度建库**：以账号（`sec_uid`）为核心，保存公开主页资料并跨来源合并去重
- **社交网络扩散**：作品作者 → 评论者 → 其主页作品，按层级 BFS 扩散，预算可控
- **网络分析**：PageRank、度/介数中心性、Louvain 社区发现、模块度与连通分量
- **用户画像**：7 类角色分类（核心影响者/跨圈桥接者/社区核心等）与 0–100 影响力分
- **用户列表页**：以账号为单位的 12 列分页表格，支持角色/性别/层级筛选与详情查看
- **用户地图**：按 IP 属地聚合账号数量，交互式省级地图，男女与未知性别分列
- **原始数据留存**：所有 API 原始响应按 SHA256 去重入库，可回溯、可重新清洗
- **零前端构建**：原生 HTML/CSS/JS，无 npm 依赖

## 目录结构

```
dyer/
├── app/                    # FastAPI Web 服务
│   ├── main.py             # 应用入口（lifespan / 静态资源）
│   ├── config.py           # 路径、端口、并发等运行配置
│   ├── db.py               # SQLite 模型（任务/作品/评论/用户/关系/原始数据/指标）
│   ├── importer.py         # 引擎 JSONL -> SQLite 幂等导入
│   ├── jobs.py             # 任务调度、子进程、日志广播
│   ├── routes.py           # HTTP / WebSocket 接口
│   ├── schemas.py          # 入参校验
│   ├── social/             # 用户维度：清洗、幂等入库、网络分析、角色画像、地区聚合
│   └── www/                # 前端页面（原生 JS）
│       └── geo/china.json  # 省级地图数据（DataV.GeoAtlas）
├── engine/                 # 裁剪后的 MediaCrawler 抖音引擎
│   ├── job_runner.py       # dyer 新增：读取任务 JSON 并启动采集
│   ├── dyer_network.py     # dyer 新增：社交网络扩散采集器（BFS + 降级）
│   ├── media_platform/douyin/
│   ├── libs/               # stealth.min.js、douyin.js（签名脚本）
│   └── LICENSE             # 上游 NON-COMMERCIAL LEARNING LICENSE 1.1
├── tests/                  # pytest（含假引擎端到端测试）
├── data/                   # 运行期数据（git 忽略）
└── run.py                  # 启动入口
```

## 页面导览

| 页面 | 内容 |
| --- | --- |
| 采集任务 | 创建关键词/作品/创作者/扩散采集任务，实时日志与扫码 |
| 数据浏览 | 作品与评论的筛选、排序、分页与导出 |
| 数据概览 | 作品/评论/点赞统计、关键词分布、每日作品量、高赞榜 |
| 用户列表 | 账号维度的分页表格（粉丝/作品/获赞/IP/性别/层级/角色/影响力/社区）与详情抽屉 |
| 用户网络 | 关键节点榜、社区结构、用户类型分布（不再渲染全量网络图，规模上万时无意义） |
| 用户地图 | 省级交互式地图，按账号数着色，悬停/点击查看男女人数 |

## 快速开始

```bash
# 一键启动：自动创建 .venv、安装依赖、检测浏览器
./run.sh

# 常用参数
./run.sh --port 8080     # 指定端口
./run.sh --host 0.0.0.0  # 允许局域网访问
./run.sh --reload        # 开发模式（自动重载）
./run.sh --check         # 只检查环境，不启动
```

`run.sh` 会依次完成：Python 版本检查 → 创建/复用 `.venv` → 按 `requirements.txt` 安装依赖（有变更才重装）→
检测 Playwright 浏览器与本地 Chrome → 启动服务。首次使用如需手动执行：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python run.py
```

打开 <http://127.0.0.1:8000> 即可使用。

### 登录说明

| 方式 | 说明 | 适用场景 |
| --- | --- | --- |
| 扫码登录 | 默认**无头**运行，二维码生成后自动在网页弹窗中展示，用抖音 App 扫码确认后自动继续；需要可见浏览器窗口时在表单里取消「无头模式」 | 默认方式 |
| Cookie 登录 | 从浏览器复制 `douyin.com` 的 Cookie，可在「设置」里保存，后续 Cookie 任务自动复用 | 无显示器的服务器 |

**浏览器通道**（任务表单可选）：

| 选项 | 说明 |
| --- | --- |
| 自动（优先本地 Chrome） | 依次尝试：项目内 `chrome-extract/` → 环境变量 `DYER_CHROME_PATH` → 系统安装的 Chrome → 内置 Chromium |
| 项目内 Chrome | 强制使用 `chrome-extract/opt/google/chrome/chrome`（把 Chrome 解压到项目根目录即可） |
| 系统 Chrome | Playwright `channel="chrome"`，使用系统安装的 Google Chrome |
| 内置 Chromium | Playwright 自带浏览器 |
| 连接本地 Chrome（CDP） | 连接用户自己以 `--remote-debugging-port=9222` 启动的 Chrome，使用真实浏览器环境与登录态 |

**环境提示**

- 若项目根目录存在 `chrome-extract/opt/google/chrome/chrome`（Chrome 解压版），引擎会自动优先使用它；实测该 Chrome 有头模式可正常打开抖音登录页并生成二维码。
- 纯无头访问抖音可能被风控（返回验证码页）；此时请使用本地 Chrome、CDP 或 Cookie 登录。
- 有头浏览器在缺少 X11 的环境下可能启动失败，引擎会自动降级为无头，二维码仍可在网页弹窗中扫码。
- 抖音对无头浏览器和数据中心 IP 有风控，可能出现「验证码中间页」。此时优先使用「连接本地 Chrome（CDP）」或 Cookie 登录。
- 扫码登录要求浏览器能正常访问 `douyin.com`；网络受限时请改用 Cookie。

首次登录成功后，登录态会保存在 `engine/browser_data/`（可通过引擎配置 `SAVE_LOGIN_STATE` 关闭），后续任务无需重复扫码。
Cookie / 登录态属于敏感信息，请勿提交到仓库或分享给他人。

### 采集参数

| 参数 | 说明 |
| --- | --- |
| 最多作品数 | 单个关键词最多抓取的作品数（抖音搜索每页固定 10 条，最小 10） |
| 单作品评论数 | 每个作品最多抓取的一级评论数，0 表示不抓评论 |
| 起始页码 | 关键词搜索从第几页开始 |
| 请求间隔 | 每次请求之间的休眠秒数，建议保持 ≥ 2，降低风控概率 |
| 采集二级评论 | 是否继续抓取评论的回复（数据量会明显增大） |
| 下载视频/图片 | 由引擎把封面、视频、图集下载到任务目录 |

## 社交网络扩散采集与用户网络

### 扩散流程

```
关键词 / 作品 / 创作者（种子）
      ↓ 抓取作品与作者
   用户主页资料 + 作品列表
      ↓ 抓取作品评论
   评论者账号（层级 +1）──────┐
      ↓ 加入待采队列           │ 受 expand_depth / max_users 约束
   继续抓取主页与作品 ←────────┘
```

- 所有响应原样写入 `data/jobs/<id>/data/network/raw.jsonl`，任务结束后由 `app/social/importer.py`
  清洗、去重、入库；原始响应同时进入 `raw_items`（按 SHA256 去重）。
- 用户以 `sec_uid` 唯一标识，作品以 `aweme_id`、评论以 `comment_id` 去重；同一用户多次出现时，
  文本字段取非空新值、计数字段取最大值、扩散层级取最小值。
- 任务完成后自动执行一次网络分析，也可在「用户网络」页点「重新分析」。

### 扩散参数

| 参数 | 说明 |
| --- | --- |
| 扩散层级 `expand_depth` | 0 只采种子用户；1 采其评论者；2 再展开评论者（上限 3） |
| 最多用户数 `max_users` | 本次任务发现账号的总预算 |
| 每用户作品数 `max_awemes_per_user` | 控制单个用户展开的作品数量 |
| 单作品评论数 | 复用现有参数，控制每个作品的评论抓取量 |
| 请求间隔 | 每个请求后的休眠秒数，建议 ≥ 2 |
| 关注列表（实验） | 抖音 Web 关注接口受限，默认关闭；开启后失败会自动跳过并记日志 |

### 用户维度数据表

| 表 | 内容 |
| --- | --- |
| `dy_users` | 账号公开资料：昵称、抖音号、签名、粉丝/关注/作品/获赞数、IP 属地、认证、扩散层级、来源 |
| `user_relations` | 关系边：`commented_on`（评论）、`replied_to`（回复）、`following`（关注，实验），带权重与证据 ID |
| `raw_items` | 原始响应（kind / external_id / 深度 / 来源 / SHA256），去重保存 |
| `user_metrics` | 最近一次分析的图指标（PageRank、入/出度、介数、社区、连通分量） |
| `network_runs` | 每次分析的汇总（节点/边/密度/模块度/社区数 + Top 列表） |

作品与评论仍写入 `videos` / `comments`，并新增 `user_sec_uid` 关联到用户维度。

### 用户角色画像

在拓扑指标之上，用一组可解释规则给每个账号打上单一角色标签（优先级自上而下）：

| 角色 | 判定规则 | 含义 |
| --- | --- | --- |
| 核心影响者 `hub` | 入度 ≥ 3 且 PageRank 前 15% | 被大量账号互动的意见领袖 |
| 跨圈桥接者 `bridge` | 跨社区邻居占比 ≥ 50% 且介数前 30% | 连接不同社区的结构洞位置 |
| 社区核心 `community_leader` | 社区内 PageRank 最高且社区规模 ≥ 3 | 单个社区的意见中心 |
| 活跃互动者 `active_commenter` | 出度 ≥ 2 且出度 > 入度 | 高频评论 / 回复者 |
| 内容创作者 `creator` | 入度 ≥ 1 或有作品 | 内容供给方 |
| 长尾账号 `peripheral` | 其余账号 | 互动较少，处于边缘 |
| 孤立账号 `isolated` | 度为 0 | 没有任何互动关系 |

影响力分（0–100）= 45% PageRank + 20% 入度 + 15% 介数 + 10% 粉丝规模 + 10% 出度，
各分项先转成百分位再加权，避免粉丝数等绝对量纲主导结果。规则优先识别影响力
（`hub` 先于 `bridge`），避免小样本图把核心作者误判为桥接节点。

### 指标与研究依据

| 指标 | 含义 | 参考 |
| --- | --- | --- |
| PageRank | 有向互动网络中的影响力，被重要账号互动会获得更高权重 | Brin & Page, 1998 |
| 入度 / 出度 | 被动互动量 / 主动互动量 | Freeman, 1978 |
| 介数中心性 | 经过该账号的最短路径比例，衡量「桥接 / 结构洞」位置 | Freeman, 1977 |
| Louvain 社区 + 模块度 | 抱团结构及其显著程度 | Blondel et al., 2008 |
| 连通分量 / 密度 / 平均度 | 网络整体形态与紧密程度 | Wasserman & Faust, 1994 |

### 能力边界

- 抖音 Web 端**不提供他人的收藏列表**，也不稳定提供关注列表。本项目以「作品—评论」互动关系为主，
  关注列表仅作为实验能力，接口失败会降级跳过（日志中会注明）。
- 扩散规模受登录态与风控影响，请保持较大的请求间隔，并控制 `max_users`。
- 采集的是**公开主页信息**。请遵守《个人信息保护法》等法规，仅用于学习研究，不得用于人肉搜索、
  骚扰或任何侵犯他人权益的用途；对外展示时建议先做脱敏处理。

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/meta` | 引擎/Playwright/浏览器安装状态、默认参数 |
| GET/PUT | `/api/settings` | 读取/保存默认 Cookie |
| POST | `/api/jobs` | 创建并启动采集任务 |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/{id}` | 任务详情 |
| POST | `/api/jobs/{id}/stop` | 停止任务 |
| POST | `/api/jobs/{id}/retry` | 用相同参数重新创建任务 |
| DELETE | `/api/jobs/{id}` | 删除任务及任务数据目录 |
| GET | `/api/jobs/{id}/logs` | 增量拉取日志（`?after=seq`） |
| GET | `/api/jobs/{id}/qrcode` | 登录二维码 PNG |
| WS | `/ws/jobs/{id}` | 实时日志与状态推送 |
| GET | `/api/videos` | 作品列表（`q` / `keyword` / `job_id` / `sort` / `order` / 分页） |
| GET | `/api/videos/{aweme_id}` | 作品详情 |
| GET | `/api/videos/{aweme_id}/comments` | 作品评论 |
| GET | `/api/stats` | 概览统计 |
| GET | `/api/export` | 导出 CSV / JSON |
| GET | `/api/users` | 用户列表（搜索 / 排序 / 角色 / 性别 / 层级 / 社区 / 分页） |
| GET | `/api/users/roles` | 用户类型分布（角色数量与占比） |
| GET | `/api/users/map` | 按 IP 属地的账号分布（总数与男/女/未知） |
| GET | `/api/users/{sec_uid}` | 用户详情（资料、指标、出入边、作品） |
| GET | `/api/network/overview` | 用户/关系/原始数据统计与最近一次分析 |
| GET | `/api/network/graph` | 图谱节点与边（可视化用） |
| POST | `/api/network/analyze` | 重新计算社交网络指标 |
| GET | `/api/raw` | 原始响应浏览 |

完整 OpenAPI 文档：<http://127.0.0.1:8000/docs>

## 工作原理

1. Web 服务为每个任务生成 `data/jobs/<id>/job.json`，以**独立子进程**运行 `engine/job_runner.py`；
   子进程隔离了 MediaCrawler 的模块级全局配置、浏览器上下文与事件循环。
2. 引擎以 `SAVE_DATA_OPTION=jsonl` 把作品/评论写到 `data/jobs/<id>/data/dy/jsonl/`。
3. 父进程逐行读取子进程输出：写日志文件、推送 WebSocket、识别二维码。
4. 任务结束后，`app/importer.py` 按 `aweme_id` / `comment_id` 幂等导入到 `data/dyer.db`（SQLite）。
5. Web 服务从 SQLite 提供列表、详情、统计与导出。
6. `network` 任务走另一条链路：引擎把「主页 / 作品 / 评论」原始响应写入 `network/raw.jsonl`，
   服务端清洗入库为 `dy_users` / `user_relations` / `raw_items`，随后自动执行一次社交网络分析。

### 对上游引擎的改动

`engine/` 是 MediaCrawler 的裁剪版，只保留抖音相关代码，主要改动：

- 保留 `media_platform/douyin`、`store/douyin`、`libs/`、`base/`、`proxy/`、`media_downloader/`、`tools/`、`cache/`、`database/`，删除其余平台；
- 新增 `job_runner.py`：任务 JSON → 引擎配置 → 启动 `DouYinCrawler`，支持 `--dry-run` 打印最终配置；
- 二维码登录时把二维码保存为文件（`config.QRCODE_SAVE_PATH`），便于 Web 展示；
- 去掉 WordCloud、Redis、MongoDB、OpenCV 等可选依赖（滑块工具改为按需导入），使安装依赖最小化；
- 新增 `dyer_network.py`：以用户为中心的 BFS 扩散采集器（种子、评论者扩散、关注列表降级）；
- 修复签名脚本与 stealth 脚本的相对路径依赖，不再要求工作目录必须是 `engine/`；
- 代理 provider 改为惰性实例化、IP 缓存改用进程内缓存；
- 数据库层与配置层裁剪为「仅抖音 + SQLite」。

## 测试

```bash
pytest -q
```

测试覆盖：JSONL 幂等导入、任务全流程（用假引擎脚本代替真实浏览器）、日志/WebSocket、二维码接口、导出、重试删除、
引擎可导入性与配置映射。

## 许可

- 本项目 Web 服务与集成代码：MIT（见 `LICENSE`）。
- `engine/`：MediaCrawler 的 NON-COMMERCIAL LEARNING LICENSE 1.1（见 `engine/LICENSE`），仅限非商业学习研究。
