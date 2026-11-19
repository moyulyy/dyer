# engine/ · 抖音采集引擎（MediaCrawler 裁剪版）

本目录是 [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 的裁剪与修改版本，
作为 dyer 的采集引擎存在，由 `app/jobs.py` 以子进程方式调用。

## 保留 / 删除

保留：`media_platform/douyin`、`store/douyin`、`model`、`base`、`cache`（内存缓存）、`config`、
`database`（仅 SQLite 与抖音表）、`media_downloader`、`proxy`、`tools`、`libs`、`var.py`。

删除：其余平台（xhs / ks / bili / wb / tieba / zhihu）、CLI 参数解析、上游 FastAPI 服务、
docs/webui 等与抖音引擎无关的内容，以及 WordCloud / Redis / MongoDB / OpenCV 相关可选能力。

## dyer 新增：dyer_network.py（扩散采集）

以用户为中心的 BFS 采集器，由 `job_runner.py` 在 `crawler_type=network` 时调用：

1. 种子：关键词搜索结果作者 / 指定作品作者 / 指定创作者；
2. 对每个用户抓主页资料、作品列表、作品评论；
3. 评论者作为下一层节点入队，受层级与人数预算约束；
4. 关注列表为实验能力，接口失败自动降级跳过。

输出统一写入 `<data_dir>/network/raw.jsonl`，每行一条原始响应：

```json
{"kind": "user_profile | aweme_list | comment_list | search_list | following_list | detail_list",
 "external_id": "...", "payload": { ...原始响应... }, "depth": 1,
 "source": "comment | keyword:xx | aweme:xx", "meta": {"author_sec_uid": "..."},
 "fetched_at": 1700000000}
```

清洗与入库由 Web 服务的 `app/social/importer.py` 完成，引擎本身不写业务表。

## dyer 新增：job_runner.py

```bash
python engine/job_runner.py --job data/jobs/<id>/job.json          # 执行采集
python engine/job_runner.py --job data/jobs/<id>/job.json --dry-run # 只打印最终生效配置
```

任务 JSON 字段：

| 字段 | 说明 |
| --- | --- |
| `crawler_type` | `search` / `detail` / `creator` |
| `keywords` | 关键词列表（search 模式） |
| `aweme_ids` | 作品 ID / 链接列表（detail 模式） |
| `creator_ids` | 创作者主页 / sec_user_id 列表（creator 模式） |
| `max_notes` | 单个关键词最多作品数 |
| `max_comments` | 单作品最多一级评论数 |
| `start_page` | 搜索起始页 |
| `enable_comments` / `enable_sub_comments` | 是否抓评论 / 二级评论 |
| `enable_media` | 是否下载媒体文件 |
| `login_type` | `qrcode` / `cookie` |
| `cookie` | Cookie 登录时的 Cookie 字符串 |
| `headless` | 是否无头运行（扫码登录会被强制关闭） |
| `sleep_sec` | 请求间隔 |
| `expand_depth` | 扩散层级（0–3） |
| `max_users` | 本次最多发现的用户数 |
| `max_awemes_per_user` | 单用户最多采集的作品数 |
| `include_following` | 是否尝试采集关注列表（实验） |
| `save_option` | 固定为 `jsonl` |
| `data_dir` | 数据输出目录 |
| `qrcode_path` | 二维码保存路径 |

## 对上游代码的其他修改

1. `tools/crawler_util.py`：`show_qrcode` 在 `config.QRCODE_SAVE_PATH` 非空时把二维码保存为 PNG，而不是弹窗显示。
2. `tools/utils.py`：`Slide` / `get_tracks` 改为按需导入，未安装 OpenCV 时不影响启动。
3. `tools/async_file_writer.py`：移除词云生成逻辑及其依赖。
4. `store/douyin/_store_impl.py`、`cache/cache_factory.py`：移除 MongoDB / Redis 实现。
5. `proxy/proxy_ip_pool.py`、`proxy/base_proxy.py`：provider 惰性实例化、IP 缓存改用进程内缓存。
6. `config/base_config.py`：默认平台改为 `dy`、关闭 CDP 模式、新增 `QRCODE_SAVE_PATH`，只加载抖音配置。
7. `database/`：模型与连接裁剪为「抖音 + SQLite」。
8. `media_platform/douyin/help.py`、`core.py`：签名脚本与 stealth 脚本改为按文件位置定位，
   不再依赖当前工作目录。
9. `var.py`、`proxy/`、`cache/`：去掉 aiomysql / Redis 硬依赖，代理 provider 惰性实例化。

## 许可

上游采用 **NON-COMMERCIAL LEARNING LICENSE 1.1**（见本目录 `LICENSE`），修改版同样受其约束：
**仅限学习与研究，不得用于商业用途**。请遵守平台服务条款与当地法律法规，控制请求频率。
