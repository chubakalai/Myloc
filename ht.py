"""HT - Hyperliquid test. BTC perp, post-only long 10% below and short 10% above mid, cancel after 10 s.
Stdlib only: keccak, secp256k1 signing and msgpack are implemented below.
Usage: python ht.py   (reads HL_KEY=0x<private key of the account or an API wallet> from .env or the environment)
Exit 0 only if both orders rested and both cancels succeeded."""
import json, math, os, sys, time
from urllib.request import Request, urlopen

URL = "https://api.hyperliquid.xyz"
COIN, DIST, NOTIONAL, WAIT = "BTC", 0.10, 12.0, 10   # NOTIONAL: usd at the long price, above the $10 minimum


# ---- keccak256 (Ethereum's, not hashlib.sha3_256: different padding) ----
def keccak(data):
    M = (1 << 64) - 1
    rol = lambda v, n: ((v << n) | (v >> (64 - n))) & M if n else v
    s = [0] * 25
    data = bytearray(data) + b"\x01"
    data += b"\x00" * (-len(data) % 136)
    data[-1] |= 0x80
    for off in range(0, len(data), 136):
        for i in range(17):
            s[i] ^= int.from_bytes(data[off + 8 * i:off + 8 * i + 8], "little")
        R = 1
        for _ in range(24):
            C = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
            D = [C[(x - 1) % 5] ^ rol(C[(x + 1) % 5], 1) for x in range(5)]
            s = [s[i] ^ D[i % 5] for i in range(25)]
            x, y, cur = 1, 0, s[1]
            for t in range(24):
                x, y = y, (2 * x + 3 * y) % 5
                s[x + 5 * y], cur = rol(cur, (t + 1) * (t + 2) // 2 % 64), s[x + 5 * y]
            for y in range(0, 25, 5):
                row = s[y:y + 5]
                for x in range(5):
                    s[y + x] = row[x] ^ (~row[(x + 1) % 5] & row[(x + 2) % 5]) & M
            for j in range(7):
                R = ((R << 1) ^ ((R >> 7) * 0x71)) % 256
                if R & 2:
                    s[0] ^= 1 << ((1 << j) - 1)
    return b"".join(v.to_bytes(8, "little") for v in s[:4])


# ---- secp256k1 signature, random k, low s ----
P = 2 ** 256 - 2 ** 32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def add(a, b):
    if a is None or b is None:
        return a or b
    if a[0] == b[0]:
        if (a[1] + b[1]) % P == 0:
            return None
        m = 3 * a[0] * a[0] * pow(2 * a[1], -1, P) % P
    else:
        m = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (m * m - a[0] - b[0]) % P
    return x, (m * (a[0] - x) - a[1]) % P


def mul(k, pt):
    r = None
    while k:
        if k & 1:
            r = add(r, pt)
        pt = add(pt, pt)
        k >>= 1
    return r


def sign_hash(h, key):
    """Return (r, s, v) with v in 27/28."""
    z = int.from_bytes(h, "big")
    while True:
        k = int.from_bytes(os.urandom(32), "big") % (N - 1) + 1
        x, y = mul(k, G)
        r = x % N
        s = pow(k, -1, N) * (z + r * key) % N
        if r and s:
            break
    v = y & 1
    if s > N // 2:
        s, v = N - s, v ^ 1
    return r, s, 27 + v


# ---- msgpack (what Hyperliquid hashes) ----
def mp(o):
    if o is True or o is False:
        return b"\xc3" if o else b"\xc2"
    if isinstance(o, int):
        if o < 128:
            return bytes([o])
        for tag, n in ((0xcc, 1), (0xcd, 2), (0xce, 4), (0xcf, 8)):
            if o < 1 << 8 * n:
                return bytes([tag]) + o.to_bytes(n, "big")
    if isinstance(o, str):
        b = o.encode()
        return (bytes([0xa0 | len(b)]) if len(b) < 32 else b"\xd9" + bytes([len(b)])) + b
    if isinstance(o, list):
        return bytes([0x90 | len(o)]) + b"".join(mp(v) for v in o)
    if isinstance(o, dict):
        return bytes([0x80 | len(o)]) + b"".join(mp(k) + mp(v) for k, v in o.items())
    raise TypeError(o)


# ---- Hyperliquid signing: action hash -> phantom agent -> EIP-712 ----
def sign(action, nonce, key):
    conn = keccak(mp(action) + nonce.to_bytes(8, "big") + b"\x00")
    word = lambda n: n.to_bytes(32, "big")
    domain = keccak(keccak(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
                    + keccak(b"Exchange") + keccak(b"1") + word(1337) + word(0))
    agent = keccak(keccak(b"Agent(string source,bytes32 connectionId)") + keccak(b"a") + conn)
    r, s, v = sign_hash(keccak(b"\x19\x01" + domain + agent), key)
    return {"r": hex(r), "s": hex(s), "v": v}


# ---- exchange calls ----
def post(path, body):
    req = Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as r:
        return json.load(r)


def send(action, key):
    nonce = int(time.time() * 1000)
    return post("/exchange", {"action": action, "nonce": nonce, "signature": sign(action, nonce, key)})


def wire(x):
    return ("%.8f" % x).rstrip("0").rstrip(".")


def statuses(res):
    return res["response"]["data"]["statuses"] if res.get("status") == "ok" else []


def secret(name):
    """Environment first, then a .env file next to this script or in the working directory."""
    if name in os.environ:
        return os.environ[name]
    for d in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        try:
            for line in open(os.path.join(d, ".env")):
                k, _, v = line.strip().removeprefix("export ").partition("=")
                if k.strip() == name:
                    return v.strip().strip("\"'")
        except OSError:
            pass
    sys.exit("%s not found in environment or .env" % name)


def address(key):
    x, y = mul(key, G)
    return "0x" + keccak(x.to_bytes(32, "big") + y.to_bytes(32, "big"))[12:].hex()


def run():
    key = int(secret("HL_KEY"), 16)
    me = address(key)
    role = post("/info", {"type": "userRole", "user": me})
    print("signer", me, json.dumps(role))
    if role.get("role") == "missing":
        sys.exit("%s is unknown to Hyperliquid: use the private key of the funded wallet, "
                 "or an API wallet approved by it" % me)
    meta = post("/info", {"type": "meta"})["universe"]
    idx = next(i for i, u in enumerate(meta) if u["name"] == COIN)
    dec = meta[idx]["szDecimals"]
    mid = float(post("/info", {"type": "allMids"})[COIN])
    low, high = mid * (1 - DIST), mid * (1 + DIST)
    size = wire(math.ceil(NOTIONAL / low * 10 ** dec) / 10 ** dec)

    def order(buy, price):   # 5 significant figures; post-only so it can never be executed
        return {"a": idx, "b": buy, "p": wire(float("%.5g" % price)), "s": size, "r": False,
                "t": {"limit": {"tif": "Alo"}}}

    print("mid %.1f size %s long @ %.1f short @ %.1f" % (mid, size, low, high))
    res = send({"type": "order", "orders": [order(True, low), order(False, high)], "grouping": "na"}, key)
    print("order", json.dumps(res))
    oids = [s["resting"]["oid"] for s in statuses(res) if "resting" in s]
    if oids:
        time.sleep(WAIT)
        res = send({"type": "cancel", "cancels": [{"a": idx, "o": o} for o in oids]}, key)
        print("cancel", json.dumps(res))
    ok = len(oids) == 2 and len(statuses(res)) == 2 and all(s == "success" for s in statuses(res))
    sys.exit(0 if ok else "FAILED: check the output above and the open orders on the exchange")


if __name__ == "__main__":
    run()
