"""``cliyard.server.executor`` — 进程内执行引擎（serve T5）。

ExecutionManager 把命令/流程执行放到后台 **daemon 线程**中运行，与 FastAPI
事件循环解耦；执行过程中 ``execute_pipeline`` / ``run_flow`` 的事件回调
逐条 push 到 ``queue.Queue``（SSE 消费），同时全量追加到内存
``Execution.steps``（轮询兜底——SSE 断线后前端改走 ``GET /api/executions/{id}``
读取已记录事件，不做 SSE 重连回放）。

终态事件约定：

* ``{"type": "done", "status", "duration_ms"}`` —— 任何路径（成功/失败/异常）
  都会在 ``finally`` 中推送并 ``done_event.set()``；
* ``{"type": "error", "message", "traceback"}`` —— 线程内异常时推送
  （traceback 截断且脱敏 spec 绝对路径）。

file 参数桥接：binder 期望 ``type: file`` 参数为本地文件路径；提交参数若为
base64（``data:...;base64,`` 前缀或纯 base64 串），executor 写入临时文件后
替换为路径再交给 execute_pipeline，执行结束清理临时文件。

用法::

    execution_id = execution_manager.submit_command(spec_dir, "user.list", {"page": 1})
    for event in execution_manager.iter_events(execution_id):
        ...  # -> EventSourceResponse 消费
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator
from uuid import uuid4

from anyio.from_thread import run as _from_thread_run

from cliyard.engine.builder import execute_pipeline
from cliyard.engine.loader import load_flows, load_service
from cliyard.engine.orchestrator import _lookup_resource_method, run_flow
from cliyard.server.context import build_service_context
from cliyard.server.history import DEFAULT_HISTORY_DB_PATH, HistoryStore

logger = logging.getLogger("cliyard.server.executor")

# 内存注册表容量上限：超限时淘汰最旧的已终态执行，防止无界增长。
MAX_EXECUTIONS = 500
# 单个 base64 file 参数解码后允许的最大字节数（超限拒绝写临时文件）。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB

# 事件名固定顺序（供测试/前端参照）：命令 validate→auth→request→response→format→done
# 事件统一格式：{"type": name, **payload, "time": iso}


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（事件时间戳）。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _sanitize_error(message: str, spec_dir: str) -> str:
    """把错误消息中的 spec 绝对路径替换为 ``<spec_dir>`` 防止泄露本地路径。"""
    text = str(message)
    try:
        resolved = str(Path(spec_dir).resolve())
    except Exception:
        resolved = str(spec_dir)
    return text.replace(resolved, "<spec_dir>")


@dataclass
class Execution:
    """单次执行的可观测状态（内存注册表条目）。

    同一实例被执行线程写（steps/queue）、SSE 与轮询线程读；
    ``steps`` 读取处用 ``list()`` 快照，避免与追加竞态。
    """

    id: str
    spec_dir: str
    kind: str  # "command" | "flow"
    target: str
    params: dict[str, Any]
    status: str  # "running" | "done" | "error"
    created_at: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _started_at: float = field(default_factory=time.perf_counter, repr=False)


def _looks_like_base64(value: str) -> bool:
    """宽松判断字符串是否像纯 base64（仅用于 ``type: file`` 参数桥接）。

    要求去空白后长度 ≥8 且为 4 的倍数，字符集为 base64 字母表。
    """
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 8 or len(compact) % 4 != 0:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", compact))


def _suffix_from_mime(header: str) -> str:
    """从 data URI 的 MIME 头推断临时文件后缀；无法推断返回空串。"""
    mime = (header or "").partition(":")[2].split(";")[0].strip().lower()
    mapping = {
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/json": ".json",
        "application/xml": ".xml",
        "application/pdf": ".pdf",
        "application/zip": ".zip",
        "application/octet-stream": ".bin",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
    }
    return mapping.get(mime, "")


def _write_base64_temp_file(value: str) -> str | None:
    """把 base64 字符串（data URI 或纯 base64）写入临时文件，返回路径。

    不是 base64（或解码失败）返回 ``None``，调用方保持原值不动；解码后
    字节数超过 ``MAX_UPLOAD_BYTES`` 同样返回 ``None`` 并记录警告。
    """
    if not isinstance(value, str) or not value:
        return None
    data: str | None = None
    suffix = ""
    if ";base64," in value:
        header, _, b64 = value.partition(";base64,")
        suffix = _suffix_from_mime(header)
        data = b64
    elif _looks_like_base64(value):
        data = value
    if data is None:
        return None
    compact = re.sub(r"\s+", "", data)
    # 解码前粗判：base64 长度 → 解码字节数近似 len*3//4，避免为超大输入做解码
    if len(compact) * 3 // 4 > MAX_UPLOAD_BYTES:
        logger.warning(
            "Rejecting base64 upload of ~%d bytes (limit %d)",
            len(compact) * 3 // 4,
            MAX_UPLOAD_BYTES,
        )
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        return None
    if len(raw) > MAX_UPLOAD_BYTES:
        logger.warning(
            "Rejecting base64 upload of %d bytes (limit %d)",
            len(raw),
            MAX_UPLOAD_BYTES,
        )
        return None
    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=suffix,
        prefix=f"cliyard-upload-{uuid4().hex[:8]}-",
    ) as f:
        f.write(raw)
        return f.name


def _request_disconnected(request: Any) -> bool:
    """尽力检测 SSE 客户端是否已断连。

    ``iter_events`` 在 sse-starlette 的 AnyIO worker 线程中运行，通过
    ``anyio.from_thread.run`` 桥接到宿主事件循环调用 async 的
    ``Request.is_disconnected()``；检测不可用（无事件循环 / 非 ASGI 场景）
    时降级为 ``False``，不中断流。单元测试可 monkeypatch 本函数。
    """
    if request is None:
        return False
    try:
        return bool(_from_thread_run(request.is_disconnected))
    except Exception:
        return False


class ExecutionManager:
    """执行注册表 + 后台线程调度器（模块级单例）。

    线程模型：每次 ``submit_*`` 新起一个 ``threading.Thread(daemon=True)``
    执行命令/流程；事件通过 ``queue.Queue`` 逐条推送，``threading.Event``
    标记终态。注册表读写用 ``self._lock`` 保护。
    """

    def __init__(self, history_store: HistoryStore | None = None) -> None:
        self._executions: dict[str, Execution] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._history_store = history_store
        self._history_lock = threading.Lock()

    @property
    def history_store(self) -> HistoryStore:
        """执行历史存储（可注入；默认 None 时首次访问惰性创建）。"""
        if self._history_store is None:
            with self._history_lock:
                if self._history_store is None:
                    self._history_store = HistoryStore(DEFAULT_HISTORY_DB_PATH)
        return self._history_store

    @history_store.setter
    def history_store(self, store: HistoryStore | None) -> None:
        self._history_store = store

    # ------------------------------------------------------------------
    # 提交入口
    # ------------------------------------------------------------------

    def submit_command(
        self,
        spec_dir: str,
        target: str,
        params: dict[str, Any] | None = None,
        http_client_factory: Callable[[], Any] | None = None,
    ) -> str:
        """提交一个 ``resource.method`` 命令执行并返回 ``execution_id``。

        Args:
            spec_dir: Spec 目录（绝对路径或相对路径，线程内 load_service）。
            target: 形如 ``user.list``（resource.method）。
            params: 提交参数（平铺 dict，按参数名匹配）。
            http_client_factory: 可选的客户端工厂（测试注入用，返回预配置
                HttpClient）。传 ``None`` 时由 execute_pipeline 自行创建。

        Returns:
            ``execution_id``（uuid4 hex）。

        Raises:
            ValueError: target 不是 ``resource.method`` 格式。
        """
        parts = target.rsplit(".", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Invalid target {target!r}: expected 'resource.method'"
            )
        execution = self._create_execution(spec_dir, "command", target, params or {})
        threading.Thread(
            target=self._run_command,
            args=(execution, target, params or {}, http_client_factory),
            daemon=True,
        ).start()
        return execution.id

    def submit_flow(
        self,
        spec_dir: str,
        flow_command: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """提交一个 flow 执行并返回 ``execution_id``。

        Flow 未知时不在提交期抛错——由后台线程查找失败并推送
        ``{"type": "error"}`` 事件（与未知命令 target 行为一致）。

        Args:
            spec_dir: Spec 目录。
            flow_command: ``_flows.yaml`` 里 FlowSpec 的 ``command``。
            params: flow 参数（供 ``{{ flow.* }}`` 模板使用）。

        Returns:
            ``execution_id``。
        """
        execution = self._create_execution(spec_dir, "flow", flow_command, params or {})
        threading.Thread(
            target=self._run_flow,
            args=(execution, flow_command, params or {}),
            daemon=True,
        ).start()
        return execution.id

    # ------------------------------------------------------------------
    # 读取 / SSE
    # ------------------------------------------------------------------

    def get(self, execution_id: str) -> Execution | None:
        """按 id 取 Execution（不存在返回 None）。"""
        with self._lock:
            return self._executions.get(execution_id)

    def iter_events(
        self, execution_id: str, request: Any = None
    ) -> Generator[dict[str, str], None, None]:
        """SSE 事件生成器：从 queue 逐条产出 ``{"event", "data"}`` dict。

        以 ``done`` 事件或 ``done_event`` 置位后队列耗尽作为结束信号；
        传入 ``request``（Starlette/FastAPI Request）时，客户端断连会提前
        终止生成器，避免后台线程空转。事件已全部入队但执行尚未开始时也能
        正确等待（``queue.get(timeout=1)``）。
        """
        execution = self.get(execution_id)
        if execution is None:
            yield {
                "event": "message",
                "data": json.dumps(
                    {"type": "error", "message": f"execution {execution_id} not found"},
                    ensure_ascii=False,
                ),
            }
            return
        checks = 0
        while True:
            try:
                event = execution.queue.get(timeout=1)
            except queue.Empty:
                if _request_disconnected(request):
                    logger.info(
                        "SSE client disconnected from execution %s; stopping stream",
                        execution_id,
                    )
                    return
                if execution.done_event.is_set():
                    return
                continue
            checks += 1
            if checks % 10 == 0 and _request_disconnected(request):
                logger.info(
                    "SSE client disconnected from execution %s; stopping stream",
                    execution_id,
                )
                return
            yield {
                "event": "message",
                "data": json.dumps(event, ensure_ascii=False),
            }
            if event.get("type") == "done":
                return

    # ------------------------------------------------------------------
    # 后台执行线程
    # ------------------------------------------------------------------

    def _run_command(
        self,
        execution: Execution,
        target: str,
        params: dict[str, Any],
        http_client_factory: Callable[[], Any] | None,
    ) -> None:
        """命令执行线程体：load_service → lookup → execute_pipeline。"""
        try:
            service = load_service(execution.spec_dir)
            resource, method_spec = _lookup_resource_method(target, service)
            service_ctx = build_service_context(execution.spec_dir, service, resource)

            # file 参数桥接：base64 → 临时文件路径（执行结束清理）
            bridged, tmp_files = self._bridge_file_params(method_spec, params)
            try:
                client = http_client_factory() if http_client_factory else None
                execute_pipeline(
                    kwargs=bridged,
                    method_spec=method_spec,
                    resource_spec=resource,
                    service_ctx=service_ctx,
                    resource_name=resource.get("name") or target.split(".")[0],
                    event_cb=lambda name, payload: self._emit(execution, name, payload),
                    **({"http_client": client} if client is not None else {}),
                )
            finally:
                self._cleanup_tmp_files(tmp_files)
            execution.status = "done"
        except Exception as exc:
            execution.status = "error"
            self._emit_error(execution, exc, execution.spec_dir)
        finally:
            self._finish(execution)

    def _run_flow(
        self,
        execution: Execution,
        flow_command: str,
        params: dict[str, Any],
    ) -> None:
        """Flow 执行线程体：load_flows 匹配 → run_flow（step_cb 透出事件）。"""
        try:
            service = load_service(execution.spec_dir)
            flow_spec = next(
                (f for f in load_flows(execution.spec_dir) if f.command == flow_command),
                None,
            )
            if flow_spec is None:
                raise ValueError(
                    f"Flow {flow_command!r} not found in spec dir {execution.spec_dir}"
                )
            service_ctx = build_service_context(execution.spec_dir, service)
            run_flow(
                flow_spec,
                params or {},
                service_ctx,
                service,
                step_cb=lambda name, payload: self._emit(execution, name, payload),
            )
            execution.status = "done"
        except Exception as exc:
            execution.status = "error"
            self._emit_error(execution, exc, execution.spec_dir)
        finally:
            self._finish(execution)

    # ------------------------------------------------------------------
    # 事件 / 生命周期
    # ------------------------------------------------------------------

    def _create_execution(
        self, spec_dir: str, kind: str, target: str, params: dict[str, Any]
    ) -> Execution:
        """创建并注册一个 Execution（status=running），返回实例。"""
        execution = Execution(
            id=uuid4().hex,
            spec_dir=str(spec_dir),
            kind=kind,
            target=target,
            params=params,
            status="running",
            created_at=_now_iso(),
        )
        with self._lock:
            self._evict_oldest_terminal()
            self._executions[execution.id] = execution
            self._order.append(execution.id)
        self._safe_record_start(execution)
        return execution

    def _evict_oldest_terminal(self) -> None:
        """注册表满时按插入顺序淘汰最旧的已终态执行；全部 running 则跳过。

        只淘汰 ``done``/``error`` 的终态条目——后台线程已结束、SSE/轮询
        均已消费完毕，移出后无并发读写风险。
        """
        if len(self._executions) < MAX_EXECUTIONS:
            return
        for execution_id in self._order:
            execution = self._executions.get(execution_id)
            if execution is not None and execution.status in ("done", "error"):
                del self._executions[execution_id]
                self._order.remove(execution_id)
                logger.info(
                    "Evicted terminal execution %s (%s) from registry",
                    execution_id,
                    execution.status,
                )
                return
        logger.warning(
            "Execution registry at capacity (%d) with no terminal entries; "
            "skipping eviction",
            MAX_EXECUTIONS,
        )

    def _emit(self, execution: Execution, name: str, payload: dict[str, Any]) -> None:
        """把 ``(name, payload)`` 事件归一化为 ``{"type", ...payload, "time"}``。"""
        event: dict[str, Any] = {"type": name, "time": _now_iso()}
        event.update(payload or {})
        with self._lock:
            execution.steps.append(event)
        execution.queue.put(event)

    def _emit_error(self, execution: Execution, exc: Exception, spec_dir: str) -> None:
        """推送错误事件（消息脱敏 spec 路径，traceback 截断）。"""
        event = {
            "type": "error",
            "time": _now_iso(),
            "message": _sanitize_error(str(exc), spec_dir),
            "traceback": _sanitize_error(traceback.format_exc(), spec_dir)[:2000],
        }
        with self._lock:
            execution.steps.append(event)
        execution.queue.put(event)

    def _finish(self, execution: Execution) -> None:
        """终态推送 ``done`` 事件并置位 done_event（任何路径都会走到）。

        顺序：先写历史库（record_finish 失败记录日志），再推 SSE done 事件、
        置位 done_event——保证 SSE 收到 done / ``done_event.wait()`` 返回时
        历史记录已落库，轮询兜底不会读到 running。
        """
        event = {
            "type": "done",
            "time": _now_iso(),
            "status": execution.status,
            "duration_ms": int((time.perf_counter() - execution._started_at) * 1000),
        }
        with self._lock:
            execution.steps.append(event)
        self._safe_record_finish(execution)
        execution.queue.put(event)
        execution.done_event.set()

    def _safe_record_start(self, execution: Execution) -> None:
        """历史落 running 记录；异常记录日志但不阻塞执行流程。"""
        try:
            self.history_store.record_start(execution)
        except Exception:
            logger.exception(
                "Failed to record execution start "
                "(id=%s kind=%s target=%s)",
                execution.id,
                execution.kind,
                execution.target,
            )

    def _safe_record_finish(self, execution: Execution) -> None:
        """历史落终态记录；异常记录日志但不阻塞 done 事件推送。"""
        try:
            self.history_store.record_finish(execution)
        except Exception:
            logger.exception(
                "Failed to record execution finish "
                "(id=%s kind=%s target=%s status=%s)",
                execution.id,
                execution.kind,
                execution.target,
                execution.status,
            )

    # ------------------------------------------------------------------
    # file 参数桥接
    # ------------------------------------------------------------------

    def _bridge_file_params(
        self, method_spec: dict[str, Any], params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """把 ``type: file`` 参数的 base64 值替换为临时文件路径。

        Returns:
            ``(bridged_params, tmp_files)``——bridged_params 是拷贝后的新 dict，
            tmp_files 是需要执行结束后清理的临时文件路径列表。
        """
        bridged = dict(params or {})
        tmp_files: list[str] = []
        for location in ("path", "query", "header", "body"):
            for param in (method_spec.get("params") or {}).get(location, []):
                if not isinstance(param, dict) or param.get("type") != "file":
                    continue
                name: str = param.get("name") or ""
                value = bridged.get(name)
                if value is None:
                    continue
                if isinstance(value, (tuple, list)):
                    value = value[0]
                path = _write_base64_temp_file(value)
                if path is not None:
                    bridged[name] = path
                    tmp_files.append(path)
        return bridged, tmp_files

    @staticmethod
    def _cleanup_tmp_files(paths: list[str]) -> None:
        """删除临时文件；清理失败记录警告（避免静默泄漏）。"""
        for path in paths:
            try:
                os.unlink(path)
            except OSError as exc:
                logger.warning(
                    "Failed to clean up temp file %s: %s", path, exc
                )


# 模块级单例：所有 /api 路由共享同一注册表
execution_manager = ExecutionManager()
