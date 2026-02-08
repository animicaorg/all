from animica.tx.canonical import canonical_sign_bytes, canonical_sign_hash
from animica.tx.types import TxBody


def test_canonical_sign_bytes_stable():
    body = TxBody(chain_id=1, nonce=7, from_addr="anim1from", to_addr="anim1to", value=10, fee=2, memo="m")
    got = canonical_sign_bytes(body=body, chain_id=1, network_id="mainnet", version=1, domain="tx")
    assert got.decode("utf-8") == '{"chain_id":1,"domain":"tx","fee":2,"from":"anim1from","memo":"m","network_id":"mainnet","nonce":7,"to":"anim1to","valid_after":0,"valid_until":0,"value":10,"version":1}'


def test_canonical_sign_hash_stable():
    b = b'{"chain_id":1,"domain":"tx","fee":2,"from":"anim1from","memo":"m","network_id":"mainnet","nonce":7,"to":"anim1to","valid_after":0,"valid_until":0,"value":10,"version":1}'
    assert canonical_sign_hash(sign_bytes=b).hex() == "cad1efa2f9a836f00992999683da79a56c83726db869166ce904f9245c35eb443abc18e966919bbe4bd5c007e3dc5154458ec213c77569b771c6019c7a2a3512"
