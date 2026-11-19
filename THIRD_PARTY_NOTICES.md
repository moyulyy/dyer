# 第三方组件声明

## MediaCrawler

- 仓库：https://github.com/NanmiCoder/MediaCrawler
- 作者：relakkes (relakkes@gmail.com)
- 许可：NON-COMMERCIAL LEARNING LICENSE 1.1（全文见 `engine/LICENSE`）

本项目 `engine/` 目录是 MediaCrawler 的裁剪与修改版本，仅保留抖音平台采集相关代码，
并新增 `job_runner.py` 作为 Web 服务的任务入口。根据上游许可：

1. 保留上游版权声明与许可声明（`engine/` 下各文件头部与 `engine/LICENSE`）；
2. 修改版仅限学习、研究目的使用，不得用于商业用途；
3. 不得用于大规模爬取或干扰平台正常运营。

## 前端

前端页面为原生 HTML / CSS / JavaScript，未引入第三方前端框架或 CDN 资源。

## Python 依赖

运行时依赖见 `requirements.txt`，均由各自的许可证授权，主要包括：
FastAPI（MIT）、Uvicorn（BSD）、SQLAlchemy（MIT）、Playwright（Apache-2.0）、
httpx（BSD）、Pydantic（MIT）、PyExecJS（MIT）、Pillow（HPND）等。
