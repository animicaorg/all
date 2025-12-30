from core.utils.merkle import compute_txs_root


def test_compute_txs_root_vectors():
    tx1 = bytes.fromhex("01" * 32)
    tx2 = bytes.fromhex("02" * 32)
    tx3 = bytes.fromhex("03" * 32)

    expected_zero = bytes.fromhex("00" * 32)
    expected_one = bytes.fromhex(
        "29f8f87d926a90ecc02e336bbadc2e512c7b155497a6ca8b86a574593d2ea58d"
    )
    expected_two = bytes.fromhex(
        "bb7eaf44188f6bd1394e085b8e3d03ffafd19f84554f1747e7ebbe4098cca851"
    )
    expected_three = bytes.fromhex(
        "8686c1d9d97cdc561d70aedd42a94fa807c3810e8fb6bf7bbf388bf5fdc8c763"
    )

    assert compute_txs_root([]) == expected_zero
    assert compute_txs_root([tx1]) == expected_one
    assert compute_txs_root([tx2, tx1]) == expected_two
    assert compute_txs_root([tx3, tx1, tx2]) == expected_three
