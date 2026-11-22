"""EHL - exchange on Hyperliquid. E's rebalance to A's allocation every 10 minutes, live. Stdlib only.
Only BTC is listed (no SPX, SENSEX): the margin is divided among ASSETS. HL_KEY=0x<private key of the funded account>.
Logs to console and to PATH/ehl.log (rotating, UTC). The private key is never logged."""
import json, logging, os, sys, time
from logging.handlers import RotatingFileHandler
from d import PATH, SYMBOLS, open_file, update, save
from a import allocate
from ht import address, post, send, secret, statuses, wire

ASSETS = ["BTC"]   # available on Hyperliquid
MAXLEV = 10.0      # leverage limit on the account value
MIN_ORDER = 10.0   # usd, exchange minimum order value
SLIP = 0.05        # IOC limit 5% through mid
STEP = 600         # seconds between runs: 144 per day

log = logging.getLogger("ehl")


def setup_logging():
    """Console (INFO) + rotating file (DEBUG, 1 MB x 5), UTC timestamps."""
    os.makedirs(PATH, exist_ok=True)
    fmt = logging.Formatter("%(asctime)sZ %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = time.gmtime
    fh = RotatingFileHandler(PATH + "ehl.log", maxBytes=1_000_000, backupCount=5)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    for h in (fh, ch):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.DEBUG)


def describe(res, order):
    """Log one order result: filled / resting / error / other."""
    sts = statuses(res)
    if not sts:
        log.error("order rejected, raw response: %s", json.dumps(res))
        return
    for s in sts:
        if "filled" in s:
            f = s["filled"]
            log.info("order FILLED oid %s size %s avg %s (requested %s limit %s)",
                     f.get("oid"), f.get("totalSz"), f.get("avgPx"), order["s"], order["p"])
        elif "resting" in s:
            log.warning("order RESTING oid %s (IOC should not rest)", s["resting"].get("oid"))
        elif "error" in s:
            log.error("order ERROR: %s", s["error"])
        else:
            log.warning("order status unexpected: %s", json.dumps(s))


def tick(key, me):
    t0 = time.time()
    log.info("tick start")
    for name in ASSETS:                           # fresh data
        rows = update(open_file(name), SYMBOLS[name])
        save(name, rows)
        log.info("data %s %d rows, last %s close %s", name, len(rows), rows[-1][0], rows[-1][4])
    meta = {u["name"]: (i, u["szDecimals"]) for i, u in enumerate(post("/info", {"type": "meta"})["universe"])}
    mids = post("/info", {"type": "allMids"})
    acct = post("/info", {"type": "clearinghouseState", "user": me})
    perps = float(acct["marginSummary"]["accountValue"])
    spot = post("/info", {"type": "spotClearinghouseState", "user": me})   # unified accounts keep margin as spot USDC
    usdc = sum(float(b["total"]) for b in spot["balances"] if b["coin"] == "USDC")
    equity = max(perps, usdc)
    log.info("account perps %.2f spot usdc %.2f -> equity %.2f", perps, usdc, equity)
    pos = {p["position"]["coin"]: p["position"] for p in acct["assetPositions"]}
    log.debug("positions on exchange: %s", json.dumps(pos))
    st = allocate(equity, ASSETS)                 # margin = current account value
    for n, s in st.items():
        log.info("alloc %s %s price %.2f worst1%% %.2f cross %s (%d sims) er %.1f%% lev %.2f weight %.1f%% alloc %.3f",
                 n, "long" if s["side"] > 0 else "short", s["price"], s["worst"],
                 "%.2f" % s["cross"] if s["cross"] else "none", s["n_cross"],
                 s["er"] * 100, s["lev"], s["weight"] * 100, s["alloc"])
        idx, dec = meta[n]
        mid = float(mids[n])
        p = pos.get(n, {})
        units = float(p.get("szi", 0))
        cap = equity * MAXLEV
        if abs(s["alloc"]) > cap:
            log.warning("%s allocation %.2f exceeds leverage cap %.2f (%.0fx): clipped", n, s["alloc"], cap, MAXLEV)
        want = max(-cap, min(s["alloc"], cap)) / s["price"]         # desired units, leverage capped
        gap = round(want - units, dec)                              # desired minus exchange, signed
        floor = max(MIN_ORDER, 10.0 ** -dec * mid)
        log.info("%s mid %.2f have %.6f want %.6f gap %.6f (%.2f usd, minimum %.2f)",
                 n, mid, units, want, gap, abs(gap) * mid, floor)
        if abs(gap) * mid >= floor:                                 # at least the minimum order
            buy = gap > 0
            order = {"a": idx, "b": buy, "p": wire(float("%.5g" % (mid * (1 + SLIP if buy else 1 - SLIP)))),
                     "s": wire(abs(gap)), "r": units * gap < 0 and abs(gap) <= abs(units),
                     "t": {"limit": {"tif": "Ioc"}}}
            log.info("order %s %s size %s limit %s reduce-only %s", n, "BUY" if buy else "SELL",
                     order["s"], order["p"], order["r"])
            log.debug("order payload: %s", json.dumps(order))
            res = send({"type": "order", "orders": [order], "grouping": "na"}, key)
            log.debug("order raw response: %s", json.dumps(res))
            describe(res, order)
        else:
            log.info("%s no trade: gap below minimum", n)
        log.info("position %s price %.2f units %.6f entry %.2f unrealized %.2f",
                 n, mid, units, float(p.get("entryPx", 0)), float(p.get("unrealizedPnl", 0)))
    log.info("tick done in %.1fs, equity %.2f (perps %.2f, spot usdc %.2f)", time.time() - t0, equity, perps, usdc)


def run():
    setup_logging()
    key = int(secret("HL_KEY"), 16)
    me = address(key)
    role = post("/info", {"type": "userRole", "user": me})
    if role.get("role") == "missing":
        log.critical("%s is unknown to Hyperliquid: use the key of the funded wallet or of an API wallet it approved", me)
        sys.exit(1)
    if role.get("role") == "agent":               # API wallet: the funds belong to the account that approved it
        me = role["data"]["user"]
    log.info("start signer %s account %s role %s assets %s maxlev %.1f step %ds argv %s",
             address(key), me, role.get("role"), ASSETS, MAXLEV, STEP, sys.argv[1:])
    if "once" in sys.argv:
        try:
            return tick(key, me)
        except Exception:
            log.exception("tick failed")
            sys.exit(1)
    fails = 0
    while True:
        wait = STEP - time.time() % STEP          # wait for the next ten-minute mark
        log.debug("sleeping %.0fs", wait)
        time.sleep(wait)
        try:
            tick(key, me)
            if fails:
                log.info("recovered after %d failed tick(s)", fails)
            fails = 0
        except Exception:                         # keep positions as they are, try again next mark
            fails += 1
            log.exception("tick failed (%d in a row), positions unchanged", fails)


if __name__ == "__main__":
    run()
