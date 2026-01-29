from capabilities.jobs.queue import JobQueue
from capabilities.jobs.types import JobKind, JobRequest, ResultRecord


def _enqueue_job(queue: JobQueue, *, idx: int) -> str:
    req = JobRequest(
        kind=JobKind.AI,
        caller=f"caller-{idx}".encode(),
        chain_id=1,
        payload={"prompt": f"hello-{idx}"},
    )
    receipt = queue.enqueue(
        req=req,
        chain_id=1,
        height=idx,
        tx_hash=f"tx-{idx}".encode(),
        caller=f"caller-{idx}".encode(),
    )
    return receipt.task_id.hex()


def test_queue_persistence_on_restart(tmp_path):
    db_path = tmp_path / "jobs.db"

    queue = JobQueue(str(db_path))
    expected_ids = {_enqueue_job(queue, idx=i) for i in range(5)}
    queue.conn.close()

    # Simulate a restart by creating a fresh queue instance on the same DB.
    restarted = JobQueue(str(db_path))

    seen: set[str] = set()
    for _ in expected_ids:
        next_item = restarted.pop_next()
        assert next_item is not None
        task_id_hex, req = next_item
        assert task_id_hex not in seen
        seen.add(task_id_hex)
        restarted.complete(
            task_id_hex,
            ResultRecord(
                task_id=bytes.fromhex(task_id_hex),
                kind=req.kind,
                success=True,
                height_available=req.height_hint or 0,
                output_digest=b"ok",
            ),
        )

    assert seen == expected_ids
    assert restarted.pop_next() is None
