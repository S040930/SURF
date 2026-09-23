from threading import Event

from app.experiment.memory_study.manager import MemoryStudyWorkerManager


def test_manager_deduplicates_web_starts():
    entered = Event()
    release = Event()

    class FakeWorker:
        def run(self, study_id: str, *, worker_id: str) -> None:
            assert study_id == "pilot-1"
            entered.set()
            release.wait(timeout=2)

    manager = MemoryStudyWorkerManager(worker_factory=FakeWorker)
    first = manager.start("pilot-1")
    assert entered.wait(timeout=2)
    assert manager.start("pilot-1") == first
    assert manager.is_running("pilot-1") is True

    release.set()
    handle = manager._handles["pilot-1"]
    handle.thread.join(timeout=2)
    assert manager.is_running("pilot-1") is False
