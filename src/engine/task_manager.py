from __future__ import annotations

import inspect
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.cancellation import Cancelled

_log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Execution status for background jobs."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class JobProgressEvent:
    """Progress event published by running workers for UI consumption."""

    job_id: str
    stage: str
    current: int
    total: int
    message: str = ""


@dataclass
class JobResultEvent:
    """Terminal event published when a job completes, fails, or is cancelled."""

    job_id: str
    status: JobStatus
    result: Any = None
    error: Exception | None = None


@dataclass
class Job:
    """Metadata and handle for a submitted background task."""

    id: str
    name: str
    callable: Callable[..., Any]
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    future: Future[Any] | None = None

    @property
    def target(self) -> Callable[..., Any]:
        return self.callable

    @target.setter
    def target(self, value: Callable[..., Any]) -> None:
        self.callable = value


class TaskManager:
    """Coordinates background worker threads and dispatches thread-safe UI events."""

    def __init__(self, max_workers: int | None = 4) -> None:
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="TaskManagerWorker",
        )
        self._jobs: dict[str, Job] = {}
        self._result_callbacks: dict[str, Callable[[JobResultEvent], None]] = {}
        self._progress_callbacks: dict[str, Callable[[JobProgressEvent], None]] = {}
        self._event_listeners: list[Callable[[JobProgressEvent | JobResultEvent], None]] = []
        self._lock = threading.Lock()
        self._event_queue: queue.Queue[JobProgressEvent | JobResultEvent] = queue.Queue()
        self._is_shutdown = False

    @property
    def has_event_listeners(self) -> bool:
        """Return True if at least one global event listener is registered."""
        with self._lock:
            return bool(self._event_listeners)

    def add_event_listener(
        self, listener: Callable[[JobProgressEvent | JobResultEvent], None]
    ) -> None:
        """Register a listener invoked for every event drained by poll_events()."""
        with self._lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    def remove_event_listener(
        self, listener: Callable[[JobProgressEvent | JobResultEvent], None]
    ) -> None:
        """Unregister a previously added global event listener."""
        with self._lock:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

    def register_callbacks(
        self,
        job_id: str,
        *,
        on_result: Callable[[JobResultEvent], None] | None = None,
        on_progress: Callable[[JobProgressEvent], None] | None = None,
    ) -> None:
        """Register or update per-job completion and progress callbacks."""
        with self._lock:
            if on_result is not None:
                self._result_callbacks[job_id] = on_result
            if on_progress is not None:
                self._progress_callbacks[job_id] = on_progress

    def submit_job(
        self,
        name: str,
        target: Callable[..., Any],
        *args: Any,
        on_result: Callable[[JobResultEvent], None] | None = None,
        on_progress: Callable[[JobProgressEvent], None] | None = None,
        **kwargs: Any,
    ) -> Job:
        """Submit a target callable for background execution.

        Creates or binds an isolated cancellation token for the job, registers
        optional per-job callbacks, and schedules execution on the thread pool.
        """
        with self._lock:
            if self._is_shutdown:
                raise RuntimeError("Cannot submit job to a shutdown TaskManager")

            job_id = uuid.uuid4().hex
            job_args = list(args)
            job_kwargs = dict(kwargs)
            cancel_event = threading.Event()

            if isinstance(job_kwargs.get("cancel_event"), threading.Event):
                cancel_event = job_kwargs["cancel_event"]

            sig = None
            try:
                sig = inspect.signature(target)
            except (ValueError, TypeError):
                pass

            if sig is not None and "cancel_event" in sig.parameters:
                try:
                    bound = sig.bind_partial(*job_args, **job_kwargs)
                    if "cancel_event" in bound.arguments:
                        existing = bound.arguments["cancel_event"]
                        if isinstance(existing, threading.Event):
                            cancel_event = existing
                        else:
                            pos_params = [
                                p.name
                                for p in sig.parameters.values()
                                if p.kind
                                in (
                                    inspect.Parameter.POSITIONAL_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                )
                            ]
                            if "cancel_event" in pos_params:
                                idx = pos_params.index("cancel_event")
                                if idx < len(job_args):
                                    job_args[idx] = cancel_event
                                else:
                                    job_kwargs["cancel_event"] = cancel_event
                            else:
                                job_kwargs["cancel_event"] = cancel_event
                    else:
                        job_kwargs["cancel_event"] = cancel_event
                except TypeError:
                    if "cancel_event" not in job_kwargs:
                        job_kwargs["cancel_event"] = cancel_event

            if on_result is not None:
                self._result_callbacks[job_id] = on_result
            if on_progress is not None:
                self._progress_callbacks[job_id] = on_progress

            job = Job(
                id=job_id,
                name=name,
                callable=target,
                args=tuple(job_args),
                kwargs=job_kwargs,
                cancel_event=cancel_event,
                status=JobStatus.PENDING,
                created_at=time.time(),
            )
            self._jobs[job_id] = job
            job.future = self._executor.submit(self._run_job, job)
            return job

    def _run_job(self, job: Job) -> Any:
        """Worker wrapper that executes the job and handles terminal state transitions."""
        with self._lock:
            if job.status == JobStatus.CANCELLED or job.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
                self._event_queue.put(
                    JobResultEvent(
                        job_id=job.id,
                        status=JobStatus.CANCELLED,
                        result=None,
                        error=None,
                    )
                )
                return None
            job.status = JobStatus.RUNNING

        try:
            result = job.callable(*job.args, **job.kwargs)
        except Cancelled as exc:
            with self._lock:
                job.status = JobStatus.CANCELLED
            self._event_queue.put(
                JobResultEvent(
                    job_id=job.id,
                    status=JobStatus.CANCELLED,
                    result=None,
                    error=exc,
                )
            )
            return None
        except Exception as exc:
            with self._lock:
                if job.cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    status = JobStatus.CANCELLED
                else:
                    job.status = JobStatus.FAILED
                    status = JobStatus.FAILED
            self._event_queue.put(
                JobResultEvent(
                    job_id=job.id,
                    status=status,
                    result=None,
                    error=exc,
                )
            )
            return None
        else:
            with self._lock:
                if job.cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    status = JobStatus.CANCELLED
                else:
                    job.status = JobStatus.COMPLETED
                    status = JobStatus.COMPLETED
            self._event_queue.put(
                JobResultEvent(
                    job_id=job.id,
                    status=status,
                    result=result,
                    error=None,
                )
            )
            return result

    def post_progress(
        self,
        job_id: str,
        stage: str,
        current: int,
        total: int,
        message: str = "",
    ) -> None:
        """Publish a progress event to the queue for UI consumer polling."""
        self._event_queue.put(
            JobProgressEvent(
                job_id=job_id,
                stage=stage,
                current=current,
                total=total,
                message=message,
            )
        )

    def poll_events(self) -> list[JobProgressEvent | JobResultEvent]:
        """Drain queued events, dispatch registered job/global callbacks, and return events."""
        events: list[JobProgressEvent | JobResultEvent] = []
        while True:
            try:
                event = self._event_queue.get_nowait()
                events.append(event)
            except queue.Empty:
                break

        if not events:
            return events

        with self._lock:
            listeners = list(self._event_listeners)

        for event in events:
            if isinstance(event, JobProgressEvent):
                with self._lock:
                    progress_cb = self._progress_callbacks.get(event.job_id)
                if progress_cb is not None:
                    try:
                        progress_cb(event)
                    except Exception as exc:
                        _log.error("Error in job progress callback (%s): %s", event.job_id, exc)
            elif isinstance(event, JobResultEvent):
                with self._lock:
                    result_cb = self._result_callbacks.pop(event.job_id, None)
                    self._progress_callbacks.pop(event.job_id, None)
                if result_cb is not None:
                    try:
                        result_cb(event)
                    except Exception as exc:
                        _log.error("Error in job result callback (%s): %s", event.job_id, exc)

            for listener in listeners:
                try:
                    listener(event)
                except Exception as exc:
                    _log.error("Error in TaskManager event listener: %s", exc)

        return events

    def get_job(self, job_id: str) -> Job | None:
        """Retrieve a job by its unique ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[Job]:
        """Return all tracked jobs."""
        with self._lock:
            return list(self._jobs.values())

    def get_active_jobs(self) -> list[Job]:
        """Return all jobs that are currently pending or running."""
        with self._lock:
            return [
                job
                for job in self._jobs.values()
                if job.status in (JobStatus.PENDING, JobStatus.RUNNING)
            ]

    def cancel_job(self, job_id: str) -> bool:
        """Signal cooperative cancellation on a job's cancel event."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.cancel_event.set()
            if job.future is not None and not job.future.running():
                job.future.cancel()
            return True

    def cancel_all(self) -> None:
        """Signal cancellation on all pending and running jobs."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.cancel_event.set()
                if job.future is not None and not job.future.running():
                    job.future.cancel()

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the underlying thread pool executor."""
        with self._lock:
            self._is_shutdown = True
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.cancel_event.set()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self) -> TaskManager:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown(wait=True)
