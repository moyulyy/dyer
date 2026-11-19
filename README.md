# dyer · 抖音数据采集

基于 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 抖音引擎的 **Web 版数据采集工具**：
在浏览器里创建采集任务、实时查看运行日志、扫码登录、浏览/检索采集结果并导出。

> **免责声明**：本项目及内置引擎仅供**学习与研究**使用。请遵守目标平台的服务条款、robots 协议与相关法律法规，
> 不得用于商业用途、大规模爬取或任何干扰平台正常运营的行为。使用本项目产生的一切后果由使用者自行承担。
>
> 内置引擎 `engine/` 源自 MediaCrawler，遵循其 **NON-COMMERCIAL LEARNING LICENSE 1.1**（见 `engine/LICENSE`），
> 因此本仓库整体在涉及该引擎的部分同样**不得用于商业用途**。

## 功能特性

- **三种采集模式**：关键词搜索、指定作品（链接 / ID / 短链）、创作者主页
- **登录方式**：扫码登录（二维码直接显示在网页里）、Cookie 登录（可保存复用）
- **实时日志**：WebSocket 推送引擎日志，按级别着色，任务结束自动汇总
- **任务管理**：创建 / 排队 / 停止 / 重试 / 删除，进程隔离，服务重启后自动清理残留状态
- **数据浏览**：作品列表筛选、排序、分页，作品详情与评论查看
- **数据导出**：CSV / JSON，导出内容与当前筛选条件一致
- **数据概览**：作品/评论/点赞统计、关键词分布、每日作品量、高赞作品榜
- **零前端构建**：原生 HTML/CSS/JS，无 npm 依赖

## 目录结构

```
dyer/
├── app/                    # FastAPI Web 服务
│   ├── main.py             # 应用入口（lifespan / 静态资源）
│   ├── config.py           # 路径、端口、并发等运行配置
│   ├── db.py               # SQLite 模型（jobs / videos / comments）
│   ├── importer.py         # 引擎 JSONL -> SQLite 幂等导入
│   ├── jobs.py             # 任务调度、子进程、日志广播
│   ├── routes.py           # HTTP / WebSocket 接口
│   ├── schemas.py          # 入参校验
│   └── www/                # 前端页面（原生 JS）
├── engine/                 # 裁剪后的 MediaCrawler 抖音引擎
│   ├── job_runner.py       # dyer 新增：读取任务 JSON 并启动采集
│   ├── media_platform/douyin/
│   ├── libs/               # stealth.min.js、douyin.js（签名脚本）
│   └── LICENSE             # 上游 NON-COMMERCIAL LEARNING LICENSE 1.1
├── tests/                  # pytest（含假引擎端到端测试）
├── data/                   # 运行期数据（git 忽略）
└── run.py                  # 启动入口
```

## 快速开始

```bash
# 1. 安装依赖（建议 Python 3.11+，本项目在 3.14 上验证通过）
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 安装 Playwright 浏览器（首次运行需要）
playwright install chromium

# 3. 启动
python run.py
# 或：uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000> 即可使用。

### 登录说明

| 方式 | 说明 | 适用场景 |
| --- | --- | --- |
| 扫码登录 | 引擎会以**可见浏览器窗口**打开抖音，二维码同时写入任务目录并展示在网页日志抽屉里；用抖音 App 扫码确认后自动继续 | 本机桌面环境 |
| Cookie 登录 | 从浏览器复制 `douyin.com` 的 Cookie，可在「设置」里保存，后续 Cookie 任务自动复用；无头模式可用 | 无显示器的服务器 |

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

完整 OpenAPI 文档：<http://127.0.0.1:8000/docs>

## 工作原理

1. Web 服务为每个任务生成 `data/jobs/<id>/job.json`，以**独立子进程**运行 `engine/job_runner.py`；
   子进程隔离了 MediaCrawler 的模块级全局配置、浏览器上下文与事件循环。
2. 引擎以 `SAVE_DATA_OPTION=jsonl` 把作品/评论写到 `data/jobs/<id>/data/dy/jsonl/`。
3. 父进程逐行读取子进程输出：写日志文件、推送 WebSocket、识别二维码。
4. 任务结束后，`app/importer.py` 按 `aweme_id` / `comment_id` 幂等导入到 `data/dyer.db`（SQLite）。
5. Web 服务从 SQLite 提供列表、详情、统计与导出。

### 对上游引擎的改动

`engine/` 是 MediaCrawler 的裁剪版，只保留抖音相关代码，主要改动：

- 保留 `media_platform/douyin`、`store/douyin`、`libs/`、`base/`、`proxy/`、`media_downloader/`、`tools/`、`cache/`、`database/`，删除其余平台；
- 新增 `job_runner.py`：任务 JSON → 引擎配置 → 启动 `DouYinCrawler`，支持 `--dry-run` 打印最终配置；
- 二维码登录时把二维码保存为文件（`config.QRCODE_SAVE_PATH`），便于 Web 展示；
- 去掉 WordCloud、Redis、MongoDB、OpenCV 等可选依赖（滑块工具改为按需导入），使安装依赖最小化；
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
