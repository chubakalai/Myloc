"""D - data partition. Three steps: open, update, save. Stdlib only."""
import calendar, csv, json, os, time
from urllib.request import Request, urlopen

PATH = os.environ.get("DATA_DIR", os.path.expanduser("~/myloc/csv")).rstrip("/") + "/"
SYMBOLS = {"BTC": "BTC-USD", "SPX": "^GSPC", "SENSEX": "^BSESN"}
HEAD = ["date", "open", "high", "low", "close", "volume"]
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d"
BEGIN = -2208988800  # 1900-01-01, before any history


def open_file(name):
    """Step 1: return existing rows (without header), or [] if no file."""
    f = PATH + name + ".csv"
    if not os.path.exists(f):
        return []
    with open(f, newline="") as h:
        return list(csv.reader(h))[1:]


def update(rows, symbol):
    """Step 2: fetch from the last row (inclusive) to now, merge."""
    start = BEGIN
    if rows:
        start = calendar.timegm(time.strptime(rows[-1][0], "%Y-%m-%d"))
        rows = rows[:-1]  # last bar may have been partial: refetch it
    url = URL.format(symbol, start, int(time.time()))
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    for i, t in enumerate(res.get("timestamp") or []):
        if q["close"][i] is None:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(t))
        rows.append([day] + [q[k][i] for k in ("open", "high", "low", "close", "volume")])
    return rows


def save(name, rows):
    """Step 3: write the file."""
    os.makedirs(PATH, exist_ok=True)
    with open(PATH + name + ".csv", "w", newline="") as h:
        w = csv.writer(h)
        w.writerow(HEAD)
        w.writerows(rows)


def run():
    for name, symbol in SYMBOLS.items():
        rows = update(open_file(name), symbol)
        save(name, rows)
        print(name, len(rows), "rows, last", rows[-1][0])


if __name__ == "__main__":
    run()
