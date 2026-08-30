import threading
import time

import pytest

from src.cancellation import Cancelled
from src.engine import (
    Job,
    JobProgressEvent,
    JobResultEvent,
    JobStatus,
    TaskManager,
)


class TestJobDataclasses:
    def test_job_status_values(self):
        assert JobStatus.PENDING == "PENDING"
        assert JobStatus.RUNNING == "RUNNING"
        assert JobStatus.COMPLETED == "COMPLETED"
        assert JobStatus.FAILED == "FAILED"
        assert JobStatus.CANCELLED == "CANCELLED"

    def test_job_progress_event(self):
        event = JobProgressEvent(
            job_id="job123",
            stage="download",
            current=5,
            total=10,
            message="Descargando 5 de 10",
        )
        assert event.job_id == "job123"
        assert event.stage == "download"
        assert event.current == 5
        assert event.total == 10
        assert event.message == "Descargando 5 de 10"

    def test_job_result_event(self):
        err = RuntimeError("Test error")
        event = JobResultEvent(
            job_id="job456",
            status=JobStatus.FAILED,
            result=None,
            error=err,
        )
        assert event.job_id == "job456"
        assert event.status == JobStatus.FAILED
        assert event.result is None
        assert event.error is err

    def test_job_properties_and_target_alias(self):
        def sample_fn(x):
            return x * 2

        job = Job(id="j1", name="sample", callable=sample_fn)
        assert job.id == "j1"
        assert job.name == "sample"
        assert job.callable is sample_fn
        assert job.target is sample_fn
        assert job.status == JobStatus.PENDING
        assert isinstance(job.cancel_event, threading.Event)

        def another_fn():
            pass

        job.target = another_fn
        assert job.callable is another_fn


class TestTaskManagerExecution:
    def test_submit_and_complete_job(self):
        with TaskManager(max_workers=2) as tm:
            def add(a, b):
                return a + b

            job = tm.submit_job("Addition", add, 3, 4)
            assert job.name == "Addition"
            assert job.future is not None

            result = job.future.result(timeout=2.0)
            assert result == 7
            assert job.status == JobStatus.COMPLETED

            events = tm.poll_events()
            result_events = [e for e in events if isinstance(e, JobResultEvent)]
            assert len(result_events) == 1
            assert result_events[0].job_id == job.id
            assert result_events[0].status == JobStatus.COMPLETED
            assert result_events[0].result == 7
            assert result_events[0].error is None

    def test_submit_with_kwargs(self):
        with TaskManager(max_workers=2) as tm:
            def greet(name, prefix="Hello"):
                return f"{prefix}, {name}!"

            job = tm.submit_job("Greeting", greet, "World", prefix="Hola")
            result = job.future.result(timeout=2.0)
            assert result == "Hola, World!"
            assert job.status == JobStatus.COMPLETED

    def test_concurrent_jobs(self):
        with TaskManager(max_workers=4) as tm:
            def worker(val):
                time.sleep(0.02)
                return val * 10

            jobs = [tm.submit_job(f"Job-{i}", worker, i) for i in range(5)]
            for i, job in enumerate(jobs):
                assert job.future.result(timeout=3.0) == i * 10
                assert job.status == JobStatus.COMPLETED

            events = tm.poll_events()
            assert len(events) == 5
            job_ids = {e.job_id for e in events if isinstance(e, JobResultEvent)}
            assert job_ids == {j.id for j in jobs}

    def test_get_job_and_get_all_jobs(self):
        with TaskManager(max_workers=2) as tm:
            job1 = tm.submit_job("Job 1", lambda: 1)
            job2 = tm.submit_job("Job 2", lambda: 2)

            assert tm.get_job(job1.id) is job1
            assert tm.get_job(job2.id) is job2
            assert tm.get_job("non-existent") is None

            all_jobs = tm.get_all_jobs()
            assert len(all_jobs) == 2
            assert job1 in all_jobs
            assert job2 in all_jobs

    def test_get_active_jobs(self):
        with TaskManager(max_workers=2) as tm:
            gate = threading.Event()

            def slow_worker():
                gate.wait(timeout=2.0)
                return "done"

            job = tm.submit_job("Slow", slow_worker)
            time.sleep(0.05)
            active = tm.get_active_jobs()
            assert job in active

            gate.set()
            job.future.result(timeout=2.0)
            assert job not in tm.get_active_jobs()


class TestTaskManagerProgressReporting:
    def test_progress_events_emitted_and_polled(self):
        with TaskManager(max_workers=2) as tm:
            def multistep_worker():
                return "finished"

            job = tm.submit_job("Multistep", multistep_worker)
            tm.post_progress(job.id, "download", 1, 3, "Part 1")
            tm.post_progress(job.id, "download", 2, 3, "Part 2")
            tm.post_progress(job.id, "crop", 3, 3, "Part 3")

            job.future.result(timeout=2.0)
            events = tm.poll_events()

            progress_events = [e for e in events if isinstance(e, JobProgressEvent)]
            result_events = [e for e in events if isinstance(e, JobResultEvent)]

            assert len(progress_events) == 3
            assert progress_events[0].stage == "download"
            assert progress_events[0].current == 1
            assert progress_events[1].current == 2
            assert progress_events[2].stage == "crop"
            assert progress_events[2].current == 3

            assert len(result_events) == 1
            assert result_events[0].status == JobStatus.COMPLETED

    def test_poll_events_clears_queue(self):
        with TaskManager(max_workers=1) as tm:
            job = tm.submit_job("Quick", lambda: "ok")
            job.future.result(timeout=2.0)

            first_poll = tm.poll_events()
            assert len(first_poll) >= 1

            second_poll = tm.poll_events()
            assert len(second_poll) == 0


class TestTaskManagerCancellation:
    def test_cancel_job_via_cancel_event(self):
        with TaskManager(max_workers=2) as tm:
            started = threading.Event()

            def cancellable_worker(cancel_event: threading.Event):
                started.set()
                while not cancel_event.is_set():
                    time.sleep(0.01)
                return "cancelled early"

            job = tm.submit_job("Cancellable", cancellable_worker)
            assert started.wait(timeout=2.0)
            success = tm.cancel_job(job.id)
            assert success is True
            assert job.cancel_event.is_set()

            job.future.result(timeout=2.0)
            assert job.status == JobStatus.CANCELLED

            events = tm.poll_events()
            result_events = [e for e in events if isinstance(e, JobResultEvent)]
            assert len(result_events) == 1
            assert result_events[0].status == JobStatus.CANCELLED

    def test_cancel_job_raising_cancelled_exception(self):
        with TaskManager(max_workers=2) as tm:
            def cancelling_worker():
                raise Cancelled()

            job = tm.submit_job("RaisingCancelled", cancelling_worker)
            job.future.result(timeout=2.0)
            assert job.status == JobStatus.CANCELLED

            events = tm.poll_events()
            result_events = [e for e in events if isinstance(e, JobResultEvent)]
            assert len(result_events) == 1
            assert result_events[0].status == JobStatus.CANCELLED
            assert isinstance(result_events[0].error, Cancelled)

    def test_cancel_nonexistent_job(self):
        with TaskManager(max_workers=1) as tm:
            assert tm.cancel_job("unknown_id") is False

    def test_cancel_all_jobs(self):
        with TaskManager(max_workers=4) as tm:
            started = threading.Barrier(3)

            def slow_worker(cancel_event: threading.Event):
                started.wait(timeout=2.0)
                while not cancel_event.is_set():
                    time.sleep(0.01)

            jobs = [tm.submit_job(f"Slow-{i}", slow_worker) for i in range(2)]
            started.wait(timeout=2.0)

            tm.cancel_all()
            for job in jobs:
                assert job.cancel_event.is_set()
                job.future.result(timeout=2.0)
                assert job.status == JobStatus.CANCELLED


class TestTaskManagerErrorHandling:
    def test_job_raising_exception(self):
        with TaskManager(max_workers=2) as tm:
            def failing_worker():
                raise ValueError("Algo salió mal en el proceso")

            job = tm.submit_job("Failing", failing_worker)
            job.future.result(timeout=2.0)
            assert job.status == JobStatus.FAILED

            events = tm.poll_events()
            result_events = [e for e in events if isinstance(e, JobResultEvent)]
            assert len(result_events) == 1
            assert result_events[0].status == JobStatus.FAILED
            assert isinstance(result_events[0].error, ValueError)
            assert str(result_events[0].error) == "Algo salió mal en el proceso"

    def test_submit_after_shutdown(self):
        tm = TaskManager(max_workers=1)
        tm.shutdown(wait=True)

        with pytest.raises(RuntimeError, match="Cannot submit job to a shutdown TaskManager"):
            tm.submit_job("AfterShutdown", lambda: None)
