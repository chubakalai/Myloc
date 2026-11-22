"""SP - spot to perp. Moves X usd of USDC from spot to the perp account every day at 00:00 UTC. Stdlib only.
Usage: python sp.py X [once]     (or SP_AMOUNT=X in .env / environment)
HL_MAIN_KEY=0x<private key of the funded account>. An API wallet cannot do transfers, so this is not HL_KEY.
Signing differs from orders: user-signed action, EIP-712 domain "HyperliquidSignTransaction" on Arbitrum (42161)."""
import json, math, sys, time
from ht import address, keccak, post, secret, sign_hash, wire

DAY = 86400
CHAIN = 42161      # Arbitrum One, signatureChainId 0xa4b1
TYPE = b"HyperliquidTransaction:UsdClassTransfer(string hyperliquidChain,string amount,bool toPerp,uint64 nonce)"


def sign_transfer(amount, to_perp, nonce, key):
    """EIP-712 hash of the UsdClassTransfer struct, then the same secp256k1 signature as ht.py."""
    word = lambda n: int(n).to_bytes(32, "big")
    domain = keccak(keccak(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
                    + keccak(b"HyperliquidSignTransaction") + keccak(b"1") + word(CHAIN) + word(0))
    body = keccak(keccak(TYPE) + keccak(b"Mainnet") + keccak(amount.encode()) + word(to_perp) + word(nonce))
    r, s, v = sign_hash(keccak(b"\x19\x01" + domain + body), key)
    return {"r": hex(r), "s": hex(s), "v": v}


def transfer(amount, key, to_perp=True):
    nonce = int(time.time() * 1000)
    action = {"type": "usdClassTransfer", "hyperliquidChain": "Mainnet", "signatureChainId": hex(CHAIN),
              "amount": amount, "toPerp": to_perp, "nonce": nonce}
    return post("/exchange", {"action": action, "nonce": nonce, "signature": sign_transfer(amount, to_perp, nonce, key)})


def available(me):
    """Free spot USDC (total minus hold)."""
    spot = post("/info", {"type": "spotClearinghouseState", "user": me})
    return sum(float(b["total"]) - float(b["hold"]) for b in spot["balances"] if b["coin"] == "USDC")


def tick(key, me, x):
    free = available(me)
    amt = math.floor(min(x, free) * 1e6) / 1e6        # never more than the spot balance
    print(time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "spot free %.2f wanted %.2f moving %.2f" % (free, x, amt))
    if amt < 1.0:                                     # nothing worth moving
        print("skip: below 1 usd")
        return True
    res = transfer(wire(amt), key)
    print("transfer", json.dumps(res))
    return res.get("status") == "ok"


def run():
    x = float(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "once" else secret("SP_AMOUNT"))
    key = int(secret("HL_MAIN_KEY"), 16)
    me = address(key)
    role = post("/info", {"type": "userRole", "user": me})
    if role.get("role") != "user":
        sys.exit("%s role is %s: transfers need the key of the account itself, not an API wallet" % (me, role.get("role")))
    if "once" in sys.argv:
        sys.exit(0 if tick(key, me, x) else 1)
    while True:
        time.sleep(DAY - time.time() % DAY)           # next 00:00 UTC
        try:
            tick(key, me, x)
        except Exception as e:                        # try again tomorrow
            print("tick failed:", e)


if __name__ == "__main__":
    run()
