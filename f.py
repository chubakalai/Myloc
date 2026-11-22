"""F - fit. Log-log line fit on normalized highs, offsets by grid search, 10y channel. Stdlib only."""
import calendar, json, math, time
from d import PATH, SYMBOLS, open_file

EXPS = range(-10, 10)        # offsets 10^-10 .. 10^9 -> 20 x 20 candidates per asset
LOOKBACK = {"BTC": 10, "SPX": 30, "SENSEX": 20}  # channel window, years


def load(name):
    """Rows -> [(ts, high, low)], skipping incomplete bars."""
    out = []
    for r in open_file(name):
        try:
            out.append((calendar.timegm(time.strptime(r[0], "%Y-%m-%d")), float(r[2]), float(r[3])))
        except ValueError:
            pass
    return out


def ols(x, y):
    """Centered least squares; None if x has no spread (huge offsets flatten the log)."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx == 0:
        return None
    b = sum((u - mx) * (v - my) for u, v in zip(x, y)) / sxx
    return my - b * mx, b


def error(a, b, lx, op, lo, span, highs):
    """Mean absolute percent residual in linear price space."""
    s = 0.0
    try:
        for x, h in zip(lx, highs):
            f = lo + (10 ** (a + b * x) - op) * span
            if f <= 0:
                return math.inf
            s += abs(h / f - 1)
    except OverflowError:
        return math.inf
    return s / len(highs)


def fit(name):
    d = load(name)
    t0, t1 = d[0][0], d[-1][0]
    highs, lows = [r[1] for r in d], [r[2] for r in d]
    lo, hi = min(lows), max(highs)
    span = hi - lo
    X = [(r[0] - t0) / (t1 - t0) for r in d]
    H = [(h - lo) / span for h in highs]
    Lw = [(l - lo) / span for l in lows]

    lxs = {e: [math.log10(x + 10.0 ** e) for x in X] for e in EXPS}
    lys = {e: [math.log10(y + 10.0 ** e) for y in H] for e in EXPS}

    best = None
    for et in EXPS:
        for ep in EXPS:
            r = ols(lxs[et], lys[ep])
            if r is None:
                continue
            a, b = r
            err = error(a, b, lxs[et], 10.0 ** ep, lo, span, highs)
            if best is None or err < best[0]:
                best = (err, et, ep, a, b)
    err, et, ep, a, b = best

    # channel: shift the line in log space through lookback-window extremes
    lx, ly = lxs[et], lys[ep]
    lw = [math.log10(y + 10.0 ** ep) for y in Lw]
    cut = t1 - LOOKBACK[name] * 365.25 * 86400
    up = max(ly[i] - (a + b * lx[i]) for i in range(len(d)) if d[i][0] >= cut)
    dn = min(lw[i] - (a + b * lx[i]) for i in range(len(d)) if d[i][0] >= cut)
    return {"t0": t0, "t1": t1, "lo": lo, "hi": hi,
            "off_time": 10.0 ** et, "off_price": 10.0 ** ep,
            "a": a, "b": b, "err": err, "up": up, "dn": dn}


def svg(name, o):
    """Plot highs (grey), fit (blue), channel top (red) and bottom (green); log price axis."""
    W, H, M = 1000, 600, 50
    d = load(name)
    d = d[::max(1, len(d) // 1000)]
    t0, t1, lo, hi = o["t0"], o["t1"], o["lo"], o["hi"]
    span = hi - lo
    y0 = math.log10(min(r[2] for r in d if r[2] > 0))
    y1 = math.log10(hi) + 0.1

    def px(t):
        return M + (t - t0) / (t1 - t0) * (W - 2 * M)

    def py(p):
        v = (math.log10(p) - y0) / (y1 - y0)
        return H - M - min(max(v, 0), 1) * (H - 2 * M)

    def curve(shift):
        pts = []
        for r in d:
            x = (r[0] - t0) / (t1 - t0)
            try:
                p = lo + (10 ** (o["a"] + shift + o["b"] * math.log10(x + o["off_time"]))
                          - o["off_price"]) * span
            except OverflowError:
                continue
            if p > 0:
                pts.append("%.1f,%.1f" % (px(r[0]), py(p)))
        return " ".join(pts)

    def line(pts, color, w=1):
        return '<polyline fill="none" stroke="%s" stroke-width="%s" points="%s"/>' % (color, w, pts)

    s = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" font-family="sans-serif" font-size="12">' % (W, H),
         '<rect width="100%" height="100%" fill="white"/>']
    for k in range(math.ceil(y0), math.floor(y1) + 1):
        y = py(10.0 ** k)
        s.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="#ddd"/>' % (M, W - M, y, y))
        s.append('<text x="5" y="%.1f">1e%d</text>' % (y + 4, k))
    for t in (t0, t1):
        s.append('<text x="%.1f" y="%d" text-anchor="middle">%s</text>' %
                 (px(t), H - 20, time.strftime("%Y-%m-%d", time.gmtime(t))))
    s.append(line(" ".join("%.1f,%.1f" % (px(r[0]), py(r[1])) for r in d), "#999"))
    s.append(line(curve(0), "blue", 2))
    s.append(line(curve(o["up"]), "red"))
    s.append(line(curve(o["dn"]), "green"))
    s.append('<text x="%d" y="20">%s  err %.1f%%  off_time %.0e  off_price %.0e</text>' %
             (M, name, o["err"] * 100, o["off_time"], o["off_price"]))
    s.append("</svg>")
    with open(PATH + name + ".svg", "w") as h:
        h.write("\n".join(s))


def run():
    out = {}
    for name in SYMBOLS:
        out[name] = fit(name)
        o = out[name]
        svg(name, o)
        print(name, "off_time %.0e off_price %.0e b %.3f err %.1f%%" %
              (o["off_time"], o["off_price"], o["b"], o["err"] * 100))
    with open(PATH + "fit.json", "w") as h:
        json.dump(out, h, indent=1)


if __name__ == "__main__":
    run()
