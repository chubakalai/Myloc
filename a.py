"""A - allocation. Block-bootstrap Monte Carlo inside the fit channel, then allocation formula. Stdlib only."""
import math, random, sys
from d import SYMBOLS, open_file
from f import fit

N, CHUNK, YEARS = 1000, 7, 4   # accepted simulations, days per chunk, horizon in years
MAX_TRIES = 200 * N            # safety stop for the rejection loop


def band(o, t, shift=0.0):
    """Fit line (shift 0), upper (o['up']) or lower (o['dn']) channel price at time t."""
    x = (t - o["t0"]) / (o["t1"] - o["t0"])
    try:
        return o["lo"] + (10 ** (o["a"] + shift + o["b"] * math.log10(x + o["off_time"]))
                          - o["off_price"]) * (o["hi"] - o["lo"])
    except OverflowError:
        return math.inf


def simulate(name, o):
    """Return (last price, accepted paths, fit line per step). Paths stay strictly inside the channel."""
    c = [float(r[4]) for r in open_file(name) if r[4] and float(r[4]) > 0]
    r = [math.log(c[i + 1] / c[i]) for i in range(len(c) - 1)]
    dt = (o["t1"] - o["t0"]) / (len(c) - 1)               # average seconds per bar
    steps = int(YEARS * 365.25 * 86400 / dt)
    ts = [o["t1"] + (k + 1) * dt for k in range(steps)]
    mid = [band(o, t) for t in ts]
    up = [band(o, t, o["up"]) for t in ts]
    dn = [band(o, t, o["dn"]) for t in ts]
    p0 = c[-1]

    def walk():
        path, p = [], p0
        while len(path) < steps:
            i = random.randrange(len(r) - CHUNK + 1)
            for x in r[i:i + CHUNK]:                       # a chunk moves together
                k = len(path)
                if k == steps:
                    break
                p *= math.exp(x)
                if not dn[k] < p < up[k]:
                    return None                            # left the channel: discard
                path.append(p)
        return path

    sims, tries = [], 0
    while len(sims) < N:
        tries += 1
        if tries > MAX_TRIES:
            raise RuntimeError("%s: only %d/%d paths stayed in channel" % (name, len(sims), N))
        s = walk()
        if s:
            sims.append(s)
    return p0, sims, mid


def stats(name, o):
    p0, sims, mid = simulate(name, o)
    k = max(1, N // 100)   # 1% of the simulations
    # average price where simulations cross the fit line (non-crossing ones are skipped)
    above = p0 > band(o, o["t1"])
    cross = []
    for s in sims:
        for i, p in enumerate(s):
            if (p > mid[i]) != above:   # first crossing only, then next simulation
                cross.append(mid[i])
                break
    cross_price = sum(cross) / len(cross) if cross else None
    er = cross_price / p0 - 1 if cross_price else 0.0      # signed: negative = price above fit = short
    side = 1 if er >= 0 else -1
    if side > 0:   # long: mean of the 10 lowest minima
        worst = sum(sorted(min(s) for s in sims)[:k]) / k
        lev = 1 / (1 - worst / p0)
    else:          # short: high of (price - 2*fit) per simulation, rank the sims by it, then add 2*fit back
        top = []
        for s in sims:
            i = max(range(len(s)), key=lambda j: s[j] - 2 * mid[j])
            v = s[i] - 2 * mid[i]
            top.append((v, v + 2 * mid[i]))   # high, then 2*fit added back = actual price
        top.sort()
        worst = sum(p for _, p in top[-k:]) / k   # mean actual price of the 10 highest
        lev = 1 / max(worst / p0 - 1, 1e-9)
    return {"price": p0, "worst": worst, "cross": cross_price, "lev": lev, "er": er,
            "side": side, "n_cross": len(cross)}


def allocate(margin, assets=None):
    """Per asset (default all, or the given list): |er| / sum|er| * margin * max leverage, signed negative for shorts."""
    st = {name: stats(name, fit(name)) for name in (assets or SYMBOLS)}
    tot = sum(abs(s["er"]) for s in st.values())
    for s in st.values():
        s["weight"] = abs(s["er"]) / tot if tot else 0.0   # expected return counts as positive for shorts too
        s["alloc"] = s["side"] * s["weight"] * margin * s["lev"]
    return st


def run(margin=1.0):
    st = allocate(margin)
    for name, s in st.items():
        print(name, "price %.2f worst1%% %.2f cross %s (%d sims) er %.1f%% %s lev %.2f weight %.1f%% alloc %.3f" %
              (s["price"], s["worst"], "%.2f" % s["cross"] if s["cross"] else "none", s["n_cross"],
               s["er"] * 100, "long" if s["side"] > 0 else "short", s["lev"], s["weight"] * 100, s["alloc"]))
    return st


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 1.0)
