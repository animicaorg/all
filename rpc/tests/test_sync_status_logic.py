from rpc.methods import sync as sync_methods


def test_sync_status_behind_node_is_syncing() -> None:
    status, behind_by, reason = sync_methods._compute_sync_status(
        local_height=927,
        best_remote_height=1666,
        allowed_lag=0,
    )

    assert status == "SYNCING"
    assert behind_by == 739
    assert reason is None


def test_sync_status_unknown_remote_is_not_synced() -> None:
    status, behind_by, reason = sync_methods._compute_sync_status(
        local_height=927,
        best_remote_height=None,
        allowed_lag=0,
    )

    assert status == "UNKNOWN_REMOTE"
    assert behind_by is None
    assert reason == "no peer tip information"


def test_sync_status_allows_small_lag() -> None:
    status, behind_by, reason = sync_methods._compute_sync_status(
        local_height=1665,
        best_remote_height=1666,
        allowed_lag=1,
    )

    assert status == "SYNCHRONIZED"
    assert behind_by == 1
    assert reason is None
