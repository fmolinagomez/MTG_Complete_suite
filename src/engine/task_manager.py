from __future__ import annotations

import inspect
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
        self._lock = threading.Lock()
        self._event_queue: queue.Queue[JobProgressEvent | JobResultEvent] = queue.Queue()
        self._is_shutdown = False

    def submit_job(
        self,
        name: str,
        target: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Job:
        """Submit a target callable for background execution.

        Creates an isolated cancellation token for the job and schedules it
        on the thread pool.
        """
        with self._lock:
            if self._is_shutdown:
                raise RuntimeError("Cannot submit job to a shutdown TaskManager")

            job_id = uuid.uuid4().hex
            cancel_event = threading.Event()

            sig = None
            try:
                sig = inspect.signature(target)
            except (ValueError, TypeError):
                pass

            job_kwargs = dict(kwargs)
            if sig is not None and "cancel_event" in sig.parameters and "cancel_event" not in job_kwargs:
                job_kwargs["cancel_event"] = cancel_event

            job = Job(
                id=job_id,
                name=name,
                callable=target,
                args=args,
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
        """Drain and return all queued progress and result events without blocking."""
        events: list[JobProgressEvent | JobResultEvent] = []
        while True:
            try:
                event = self._event_queue.get_nowait()
                events.append(event)
            except queue.Empty:
                break
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
                job for job in self._jobs.values()
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
