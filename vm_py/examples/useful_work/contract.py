"""Simple demo contract that scores a mined block using header fields."""


def score_block(
    block_hash: bytes,
    height: int,
    timestamp: int,
    difficulty: int = 0,
    miner: bytes | None = None,
) -> dict:
    digest = sum(block_hash) % 256
    parity = 0 if digest % 2 == 0 else 1
    miner_checksum = sum(miner) % 256 if miner else 0

    score = (digest ^ (height & 0xFF) ^ (timestamp & 0xFF)) + (difficulty & 0x3F)
    composite = (
        ((digest & 0xFF) << 24)
        | ((miner_checksum & 0xFF) << 16)
        | ((parity & 0xFF) << 8)
        | (score & 0xFF)
    )

    return composite
