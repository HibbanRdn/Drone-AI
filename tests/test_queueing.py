from __future__ import annotations

from gap_plot_ai.queueing import LatestFrameQueue


def test_bounded_queue_drops_oldest_frame() -> None:
    queue: LatestFrameQueue[int] = LatestFrameQueue(capacity=2)
    assert queue.put(1)
    assert queue.put(2)
    assert queue.put(3)
    assert queue.get(timeout=0) == 2
    assert queue.get(timeout=0) == 3
    stats = queue.stats()
    assert stats.dropped == 1
    assert stats.pushed == 3
    assert stats.popped == 2


def test_queue_close_unblocks_and_rejects_new_items() -> None:
    queue: LatestFrameQueue[int] = LatestFrameQueue(capacity=1)
    queue.close()
    assert queue.get(timeout=0) is None
    assert not queue.put(1)
    assert queue.stats().closed
