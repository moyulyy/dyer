# -*- coding: utf-8 -*-
"""采集任务调度：子进程运行引擎、实时日志广播、结果导入。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app import config
from app.db import Job, SessionLocal
from app.importer import import_job_data
from app.schemas import JobCreate

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"


def _now() -> int:
    return int(time.time())


def _level_of(text: str) -> str:
    lowered = text.lower()
    if "traceback" in lowered or " error" in lowered or "error]" in lowered or "失败" in text:
        return "error"
    if "warn" in lowered or "警告" in text:
        return "warning"
    if "成功" in text or "finished" in lowered or "done" in lowered:
        return "success"
    return "info"


def job_to_dict(job: Job) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    try:
        params = json.loads(job.params or "{}")
    except json.JSONDecodeError:
        params = {}
    return {
        "id": job.id,
        "crawler_type": job.crawler_type,
        "keywords": job.keywords,
        "params": params,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "exit_code": job.exit_code,
        "error": job.error,
        "video_count": job.video_count,
        "comment_count": job.comment_count,
        "user_count": job.user_count,
        "relation_count": job.relation_count,
        "has_qrcode": bool(job.qrcode_path and Path(job.qrcode_path).exists()),
    }


class JobManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._tasks: set[asyncio.Task] = set()
        self._logs: Dict[str, List[Dict[str, Any]]] = {}
        self._seq: Dict[str, int] = {}
        self._subscribers: Dict[str, set[asyncio.Queue]] = {}
        self._stop_requested: set[str] = set()
        self._worker: Optional[asyncio.Task] = None
        self._closing = False

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        # 队列和信号量绑定当前事件循环，服务（或测试中的 TestClient）重新启动时重建，
        # 避免沿用上一个循环里创建的对象。
        self._closing = False
        self._queue = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
        self._worker = asyncio.create_task(self._consume_queue(), name="dyer-job-worker")

    async def shutdown(self) -> None:
        self._closing = True
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, RuntimeError):
                pass
            self._worker = None
        for job_id in list(self._processes):
            await self.stop_job(job_id)
        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ 任务创建
    async def create_job(self, payload: JobCreate) -> Dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job_dir = config.JOBS_DIR / job_id
        data_dir = job_dir / "data"
        log_path = job_dir / "job.log"
        qrcode_path = job_dir / "qrcode.png"
        job_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        spec = {
            "job_id": job_id,
            "crawler_type": payload.crawler_type,
            "keywords": payload.keywords,
            "aweme_ids": payload.aweme_ids,
            "creator_ids": payload.creator_ids,
            "max_notes": payload.max_notes,
            "max_comments": payload.max_comments,
            "start_page": payload.start_page,
            "enable_comments": payload.enable_comments,
            "enable_sub_comments": payload.enable_sub_comments,
            "enable_media": payload.enable_media,
            "login_type": payload.login_type,
            "cookie": payload.cookie,
            "headless": payload.headless,
            "browser_channel": payload.browser_channel,
            "sleep_sec": payload.sleep_sec,
            "expand_depth": payload.expand_depth,
            "max_users": payload.max_users,
            "max_awemes_per_user": payload.max_awemes_per_user,
            "include_following": payload.include_following,
            "save_option": "jsonl",
            "data_dir": str(data_dir),
            "qrcode_path": str(qrcode_path),
        }
        (job_dir / "job.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        params = payload.model_dump()
        # Cookie 可能很长，列表接口里不回传明文
        params["cookie"] = "***" if payload.cookie else ""

        async with SessionLocal() as session:
            job = Job(
                id=job_id,
                crawler_type=payload.crawler_type,
                keywords=",".join(payload.keywords),
                params=json.dumps(params, ensure_ascii=False),
                status=STATUS_QUEUED,
                created_at=_now(),
                log_path=str(log_path),
                data_dir=str(job_dir),
                qrcode_path=str(qrcode_path),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            result = job_to_dict(job)

        await self._queue.put(job_id)
        self._broadcast(job_id, {"type": "status", "status": STATUS_QUEUED, "job": result})
        return result

    async def retry_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job or job.status in (STATUS_RUNNING, STATUS_QUEUED):
                return None
            spec_path = Path(job.data_dir) / "job.json"
        if not spec_path.exists():
            return None
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        payload = JobCreate(
            crawler_type=spec["crawler_type"],
            keywords=spec.get("keywords", []),
            aweme_ids=spec.get("aweme_ids", []),
            creator_ids=spec.get("creator_ids", []),
            max_notes=spec.get("max_notes", 20),
            max_comments=spec.get("max_comments", 20),
            start_page=spec.get("start_page", 1),
            enable_comments=spec.get("enable_comments", True),
            enable_sub_comments=spec.get("enable_sub_comments", False),
            enable_media=spec.get("enable_media", False),
            login_type=spec.get("login_type", "qrcode"),
            cookie=spec.get("cookie", ""),
            headless=spec.get("headless", True),
            browser_channel=spec.get("browser_channel", "auto"),
            sleep_sec=spec.get("sleep_sec", 2.0),
            expand_depth=spec.get("expand_depth", 2),
            max_users=spec.get("max_users", 50),
            max_awemes_per_user=spec.get("max_awemes_per_user", 10),
            include_following=spec.get("include_following", False),
        )
        return await self.create_job(payload)

    # ------------------------------------------------------------------ 执行
    async def _consume_queue(self) -> None:
        while True:
            job_id = await self._queue.get()
            task = asyncio.create_task(self._run_job_guarded(job_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_job_guarded(self, job_id: str) -> None:
        async with self._semaphore:
            try:
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 保证任何异常都能落到任务状态上
                await self._finish(job_id, STATUS_FAILED, error=str(exc))

    async def _run_job(self, job_id: str) -> None:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            if job.status != STATUS_QUEUED:
                # 排队期间被取消
                return
            spec_path = Path(job.data_dir) / "job.json"
            log_path = Path(job.log_path)
            job.status = STATUS_RUNNING
            job.started_at = _now()
            await session.commit()

        self._append_log(job_id, f"[dyer] 任务 {job_id} 启动", level="info")
        self._broadcast(
            job_id, {"type": "status", "status": STATUS_RUNNING, "job": {"id": job_id}}
        )

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(config.ENGINE_DIR)

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(config.ENGINE_DIR / "job_runner.py"),
            "--job",
            str(spec_path),
            cwd=str(config.ENGINE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        self._processes[job_id] = process
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if job:
                job.pid = process.pid
                await session.commit()

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    continue
                log_file.write(line + "\n")
                log_file.flush()
                self._append_log(job_id, line)
        return_code = await process.wait()
        self._processes.pop(job_id, None)

        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            data_dir = job.data_dir if job else str(config.JOBS_DIR / job_id)
            crawler_type = job.crawler_type if job else "search"
            stopped = job_id in self._stop_requested
        self._stop_requested.discard(job_id)

        imported = {"videos": 0, "comments": 0, "users": 0, "relations": 0}
        try:
            if crawler_type == "network":
                from app.social.importer import import_network_data

                imported = await import_network_data(job_id, Path(data_dir) / "data")
                self._append_log(
                    job_id,
                    "[dyer] 已入库 {} 个用户、{} 条关系、{} 个作品、{} 条评论（原始响应 {} 条）".format(
                        imported["users"], imported["relations"], imported["videos"],
                        imported["comments"], imported["raw"],
                    ),
                    level="success",
                )
                if imported.get("users") or imported.get("relations"):
                    try:
                        from app.social.graph import analyze_network

                        run_id = await analyze_network(job_id=job_id)
                        self._append_log(
                            job_id, f"[dyer] 社交网络分析完成（分析批次 #{run_id}）", level="success"
                        )
                    except Exception as exc:
                        self._append_log(job_id, f"[dyer] 社交网络分析失败: {exc}", level="warning")
                else:
                    self._append_log(job_id, "[dyer] 未采集到数据，跳过社交网络分析", level="warning")
            else:
                imported = await import_job_data(job_id, Path(data_dir) / "data")
                if imported["videos"]:
                    self._append_log(
                        job_id,
                        f"[dyer] 已导入 {imported['videos']} 个作品、{imported['comments']} 条评论",
                        level="success",
                    )
        except Exception as exc:
            self._append_log(job_id, f"[dyer] 导入数据失败: {exc}", level="error")

        if stopped:
            status, error = STATUS_STOPPED, None
        elif return_code == 0:
            status, error = STATUS_SUCCESS, None
        else:
            status, error = STATUS_FAILED, f"引擎退出码 {return_code}"
        await self._finish(
            job_id,
            status,
            exit_code=return_code,
            error=error,
            video_count=imported["videos"],
            comment_count=imported["comments"],
            user_count=imported.get("users", 0),
            relation_count=imported.get("relations", 0),
        )

    async def _finish(
        self,
        job_id: str,
        status: str,
        *,
        exit_code: Optional[int] = None,
        error: Optional[str] = None,
        video_count: int = 0,
        comment_count: int = 0,
        user_count: int = 0,
        relation_count: int = 0,
    ) -> None:
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            job.status = status
            job.finished_at = _now()
            job.exit_code = exit_code
            job.error = error
            job.video_count = video_count or job.video_count
            job.comment_count = comment_count or job.comment_count
            job.user_count = user_count or job.user_count
            job.relation_count = relation_count or job.relation_count
            job.pid = None
            await session.commit()
            await session.refresh(job)
            payload = job_to_dict(job)
        level = "error" if status == STATUS_FAILED else "success"
        self._append_log(job_id, f"[dyer] 任务结束：{status}" + (f"（{error}）" if error else ""), level=level)
        self._broadcast(job_id, {"type": "status", "status": status, "job": payload})

    async def stop_job(self, job_id: str) -> bool:
        self._stop_requested.add(job_id)
        process = self._processes.get(job_id)
        if not process:
            async with SessionLocal() as session:
                job = await session.get(Job, job_id)
                if job and job.status in (STATUS_QUEUED, STATUS_RUNNING):
                    job.status = STATUS_STOPPED
                    job.finished_at = _now()
                    job.error = "任务已取消"
                    await session.commit()
                    await session.refresh(job)
                    payload = job_to_dict(job)
                    self._broadcast(job_id, {"type": "status", "status": STATUS_STOPPED, "job": payload})
                    return True
            return False

        self._append_log(job_id, "[dyer] 收到停止请求，正在结束浏览器进程…", level="warning")
        try:
            os.killpg(os.getpgid(process.pid), 15)
        except ProcessLookupError:
            return True
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(process.pid), 9)
            except ProcessLookupError:
                pass
        return True

    # ------------------------------------------------------------------ 日志
    def _append_log(self, job_id: str, text: str, level: Optional[str] = None) -> None:
        seq = self._seq.get(job_id, 0) + 1
        self._seq[job_id] = seq
        entry = {
            "seq": seq,
            "ts": _now(),
            "level": level or _level_of(text),
            "text": text,
        }
        buffer = self._logs.setdefault(job_id, [])
        buffer.append(entry)
        if len(buffer) > config.LOG_BUFFER_SIZE:
            del buffer[: len(buffer) - config.LOG_BUFFER_SIZE]
        self._broadcast(job_id, {"type": "log", **entry})

    def get_logs(self, job_id: str, after: int = 0) -> List[Dict[str, Any]]:
        return [entry for entry in self._logs.get(job_id, []) if entry["seq"] > after]

    async def load_logs_from_file(self, job_id: str) -> List[Dict[str, Any]]:
        """进程重启后内存日志已丢失，从 job.log 还原一份。"""
        if self._logs.get(job_id):
            return self.get_logs(job_id)
        async with SessionLocal() as session:
            job = await session.get(Job, job_id)
        if not job or not job.log_path or not Path(job.log_path).exists():
            return []
        for line in Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                self._append_log(job_id, line)
        return self.get_logs(job_id)

    # ------------------------------------------------------------------ 订阅
    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(job_id)
        if subscribers:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(job_id, None)

    def _broadcast(self, job_id: str, payload: Dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(job_id, ())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------ 设置
    def load_settings(self) -> Dict[str, Any]:
        if not config.SETTINGS_PATH.exists():
            return {}
        try:
            return json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_settings(self, values: Dict[str, Any]) -> Dict[str, Any]:
        current = self.load_settings()
        current.update(values)
        config.SETTINGS_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return current

    # ------------------------------------------------------------------ 磁盘
    def delete_job_files(self, job_dir: Path) -> None:
        if job_dir.exists() and job_dir.is_relative_to(config.JOBS_DIR):
            shutil.rmtree(job_dir, ignore_errors=True)


job_manager = JobManager()


async def count_jobs() -> Dict[str, int]:
    async with SessionLocal() as session:
        result = await session.execute(select(Job))
        jobs = result.scalars().all()
    stats = {STATUS_QUEUED: 0, STATUS_RUNNING: 0, STATUS_SUCCESS: 0, STATUS_FAILED: 0, STATUS_STOPPED: 0}
    for job in jobs:
        stats[job.status] = stats.get(job.status, 0) + 1
    return stats
