# Build a tiny transfer with deliberately low gasPrice for admission failure tests.
# Schema matches earlier fixtures: string keys, deterministic CBOR map ordering.

def _hdr(major, ai): return bytes([(major<<5)|ai])
def cbor_uint(n:int)->bytes:
    if n<24: return _hdr(0,n)
    if n<256: return _hdr(0,24)+n.to_bytes(1,'big')
    if n<65536: return _hdr(0,25)+n.to_bytes(2,'big')
    if n<4294967296: return _hdr(0,26)+n.to_bytes(4,'big')
    return _hdr(0,27)+n.to_bytes(8,'big')
def cbor_bytes(b:bytes)->bytes:
    l=len(b)
    if l<24: h=_hdr(2,l)
    elif l<256: h=_hdr(2,24)+bytes([l])
    elif l<65536: h=_hdr(2,25)+l.to_bytes(2,'big')
    elif l<4294967296: h=_hdr(2,26)+l.to_bytes(4,'big')
    else: h=_hdr(2,27)+l.to_bytes(8,'big')
    return h+b
def cbor_text(s:str)->bytes:
    b=s.encode(); l=len(b)
    if l<24: h=_hdr(3,l)
    elif l<256: h=_hdr(3,24)+bytes([l])
    elif l<65536: h=_hdr(3,25)+l.to_bytes(2,'big')
    elif l<4294967296: h=_hdr(3,26)+l.to_bytes(4,'big')
    else: h=_hdr(3,27)+l.to_bytes(8,'big')
    return h+b
def cbor_null()->bytes: return bytes([0xF6])
def cbor_array(xs)->bytes:
    l=len(xs)
    if l<24: h=_hdr(4,l)
    elif l<256: h=_hdr(4,24)+bytes([l])
    elif l<65536: h=_hdr(4,25)+l.to_bytes(2,'big')
    else: h=_hdr(4,26)+l.to_bytes(4,'big')
    return h+b''.join(xs)
def cbor_map(d:dict)->bytes:
    items=sorted(d.items(), key=lambda kv: kv[0])
    l=len(items)
    if l<24: h=_hdr(5,l)
    elif l<256: h=_hdr(5,24)+bytes([l])
    elif l<65536: h=_hdr(5,25)+l.to_bytes(2,'big')
    else: h=_hdr(5,26)+l.to_bytes(4,'big')
    body=b''
    for k,v in items:
        body+=cbor_text(k); body+=to_cbor(v)
    return h+body
def to_cbor(x)->bytes:
    if x is None: return cbor_null()
    if isinstance(x,bool): return bytes([0xF5 if x else 0xF4])
    if isinstance(x,int): return cbor_uint(x) if x>=0 else None
    if isinstance(x,bytes): return cbor_bytes(x)
    if isinstance(x,str): return cbor_text(x)
    if isinstance(x,list): return cbor_array([to_cbor(i) for i in x])
    if isinstance(x,dict): return cbor_map(x)
    raise TypeError(type(x))

FROM = bytes.fromhex("bb"*20)
TO   = bytes.fromhex("cc"*20)

tx = {
    "accessList": [],
    "chainId": 1337,
    "data": b"",          # simple transfer
    "from": FROM,
    "gasLimit": 21_000,
    "gasPrice": 1,        # <<< intentionally too low
    "nonce": 0,
    "sig": None,          # left empty for mempool stateless fee-path tests
    "to": TO,
    "value": 12345
}

blob = to_cbor(tx)

from pathlib import Path
import hashlib, os
out = Path(os.path.expanduser("~/animica/mempool/fixtures/txs_cbor/tx_low_fee.cbor"))
out.write_bytes(blob)
print("wrote:", out)
print("bytes:", len(blob))
print("sha256:", hashlib.sha256(blob).hexdigest())
