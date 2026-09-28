"""
HTTP Bridge: Stock Price Service -> Dashboard Frontend

Local-dev replacement for the IdealData socket bridge in main.ipynb.
Polls the LAN price cache (ingestor service) and POSTs the same payloads
main.ipynb's combined bridge sends, so the frontend needs no changes.

    http://192.168.1.106:8000/api/prices  ->  bridge_http.py  ->  http://127.0.0.1:8000

The price service paginates via limit/offset (added to api_server.py /
read_repository.py to fix alphabetical truncation past 10k rows), so
fetch_snapshot() below pages through the full universe instead of doing
a single capped bulk call.

Usage:
    python3 bridge_http.py
"""

import datetime as dt
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

PRICES_BASE_URL = os.environ.get("PRICES_BASE_URL", "http://192.168.1.106:8000")
PRICES_LIST_ENDPOINT = f"{PRICES_BASE_URL}/api/prices"
PRICES_PAGE_LIMIT = 10000
PRICES_SYMBOL_ENDPOINT = f"{PRICES_BASE_URL}/api/prices/{{symbol}}"

FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:5173")
FRONTEND_SPOT_ENDPOINT = f"{FRONTEND_BASE_URL}/api/spot"
FRONTEND_FUTURES_RATES_ENDPOINT = f"{FRONTEND_BASE_URL}/api/futures-rates"
FRONTEND_OPTIONS_CHAIN_ENDPOINT = f"{FRONTEND_BASE_URL}/api/options-chain"

POLL_INTERVAL_SEC = 2.0
LOG_INTERVAL_SEC = 60.0

TARGET_TICKERS = [
    "AEFES","AKBNK","AKSEN","ALARK","ARCLK","ASELS","ASTOR","BIMAS","BRSAN","CIMSA",
    "DOAS","DOHOL","EKGYO","ENKAI","ENJSA","EREGL","FROTO","GARAN","GUBRF","HALKB",
    "HEKTS","ISCTR","KCHOL","TRMET","TRALT","KRDMD","MGROS","ODAS","OYAKC",
    "PETKM","PGSUS","SAHOL","SASA","SISE","SOKM","TAVHL","TCELL","THYAO","TKFEN",
    "TOASO","TSKB","TTKOM","TUPRS","ULKER","VAKBN","VESTL","YKBNK",
]

TR_PUBLIC_HOLIDAYS_2026 = {
    "2026-01-01",
    "2026-03-19", "2026-03-20", "2026-03-21", "2026-03-22",
    "2026-04-23",
    "2026-05-01", "2026-05-19",
    "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30",
    "2026-07-15",
    "2026-08-30",
    "2026-10-28", "2026-10-29",
}


# --- Maturity / DTM helpers (from main.ipynb Cell 7) ---

def _ymd(d):
    return d.strftime("%Y-%m-%d")


def is_working_day_tr(d):
    if d.weekday() >= 5:
        return False
    if d.year == 2026 and _ymd(d) in TR_PUBLIC_HOLIDAYS_2026:
        return False
    return True


def add_business_days_tr(start_dt, n_days):
    d = start_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    left = max(0, int(n_days))
    while left > 0:
        d += dt.timedelta(days=1)
        if is_working_day_tr(d):
            left -= 1
    return d


def last_business_day_of_month_tr(year, month):
    if month == 12:
        d = dt.datetime(year + 1, 1, 1, 12, 0, 0) - dt.timedelta(days=1)
    else:
        d = dt.datetime(year, month + 1, 1, 12, 0, 0) - dt.timedelta(days=1)
    while not is_working_day_tr(d):
        d -= dt.timedelta(days=1)
    return d


def get_active_maturities(base_dt=None):
    if base_dt is None:
        base_dt = dt.datetime.now()

    start_t2 = add_business_days_tr(base_dt, 2)
    out = []
    for offset in (0, 1, 2):
        month_index0 = base_dt.month - 1 + offset
        year = base_dt.year + (month_index0 // 12)
        month = (month_index0 % 12) + 1

        expiry = last_business_day_of_month_tr(year, month)
        expiry_t2 = add_business_days_tr(expiry, 2)
        dtm = max(0, (expiry_t2.date() - start_t2.date()).days)

        out.append(
            {
                "year": year,
                "month": month,
                "code": f"{month:02d}{str(year)[-2:]}",
                "label": dt.datetime(year, month, 1).strftime("%b"),
                "dtm": dtm,
            }
        )
    return out


def build_futures_symbol_map(base_dt=None):
    maturities = get_active_maturities(base_dt=base_dt)
    symbol_map = {}
    for t in TARGET_TICKERS:
        for m in maturities:
            fut_symbol = f"F_{t}{m['code']}"
            symbol_map[fut_symbol] = {
                "underlying": t,
                "maturity_code": m["code"],
                "maturity_label": m["label"],
                "dtm": m["dtm"],
            }
    return maturities, symbol_map


def calc_annualized_yield(spot_mid, fut_mid, dtm):
    if spot_mid is None or fut_mid is None:
        return None
    if spot_mid <= 0 or dtm <= 0:
        return None
    return ((fut_mid - spot_mid) / spot_mid) / dtm * 365.0


# --- Black-Scholes / IV helpers (from main.ipynb Cell 6) ---

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_d1(spot, strike, rate, t, sigma):
    if spot is None or strike is None or rate is None or t is None or sigma is None:
        return None
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return None
    num = math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t
    den = sigma * math.sqrt(t)
    if den <= 0:
        return None
    return num / den


def _bs_price(option_type, spot, strike, rate, t, sigma):
    d1 = _bs_d1(spot, strike, rate, t, sigma)
    if d1 is None:
        return None
    d2 = d1 - sigma * math.sqrt(t)
    df = math.exp(-rate * t)
    if option_type == "C":
        return spot * _norm_cdf(d1) - strike * df * _norm_cdf(d2)
    if option_type == "P":
        return strike * df * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return None


def _bs_delta(option_type, spot, strike, rate, t, sigma):
    if sigma is None:
        return None
    d1 = _bs_d1(spot, strike, rate, t, sigma)
    if d1 is None:
        return None
    if option_type == "C":
        return _norm_cdf(d1)
    if option_type == "P":
        return _norm_cdf(d1) - 1.0
    return None


def _implied_vol_bisect(option_type, market_price, spot, strike, rate, t, tol=1e-8, max_iter=120):
    if market_price is None or spot is None or strike is None or rate is None or t is None:
        return None
    if market_price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None
    df = math.exp(-rate * t)
    if option_type == "C":
        lower_bound = max(0.0, spot - strike * df)
        upper_bound = spot
    else:
        lower_bound = max(0.0, strike * df - spot)
        upper_bound = strike * df
    if market_price < lower_bound - 1e-8 or market_price > upper_bound + 1e-8:
        return None
    lo, hi = 1e-6, 5.0
    p_lo = _bs_price(option_type, spot, strike, rate, t, lo)
    p_hi = _bs_price(option_type, spot, strike, rate, t, hi)
    if p_lo is None or p_hi is None:
        return None
    if market_price <= p_lo:
        return lo
    if market_price >= p_hi:
        return hi
    for _ in range(max_iter):
        mid_v = 0.5 * (lo + hi)
        p_mid = _bs_price(option_type, spot, strike, rate, t, mid_v)
        if p_mid is None:
            return None
        err = p_mid - market_price
        if abs(err) <= tol:
            return mid_v
        if err > 0:
            hi = mid_v
        else:
            lo = mid_v
    return 0.5 * (lo + hi)


def _iv_mid(iv_bid, iv_ask):
    vals = [v for v in (iv_bid, iv_ask) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# --- Snapshot builders (from main.ipynb Cell 6) ---

def _build_rates_snapshot(
    maturities, futures_symbol_map, spot_mid, fut_mid,
    spot_bid=None, spot_ask=None, fut_bid=None, fut_ask=None,
):
    spot_bid = spot_bid or {}
    spot_ask = spot_ask or {}
    fut_bid = fut_bid or {}
    fut_ask = fut_ask or {}
    rates_by_ticker = {t: {} for t in TARGET_TICKERS}
    for fut_symbol, meta in futures_symbol_map.items():
        t = meta["underlying"]
        code = meta["maturity_code"]
        dtm = meta["dtm"]
        smid = spot_mid.get(t)
        fmid = fut_mid.get(fut_symbol)
        sbid = spot_bid.get(t)
        sask = spot_ask.get(t)
        fbid = fut_bid.get(fut_symbol)
        fask = fut_ask.get(fut_symbol)
        rates_by_ticker[t][code] = {
            "spot_mid": smid,
            "fut_mid": fmid,
            "dtm": dtm,
            "rate": calc_annualized_yield(smid, fmid, dtm),
            # Bid yield uses futures BID vs spot BID; ask yield uses futures
            # ASK vs spot ASK (each side priced consistently against itself,
            # not crossed against the other side or the mid).
            "spot_bid": sbid,
            "spot_ask": sask,
            "fut_bid": fbid,
            "fut_ask": fask,
            "bid_rate": calc_annualized_yield(sbid, fbid, dtm),
            "ask_rate": calc_annualized_yield(sask, fask, dtm),
            "fut_symbol": fut_symbol,
        }
    return rates_by_ticker


OPTION_SYMBOL_RE = re.compile(r"^O_([A-Z0-9]+)E(\d{4})([CP])(\d+(?:\.\d+)?)$")


def _parse_option_symbol(symbol: str):
    m = OPTION_SYMBOL_RE.match(symbol)
    if not m:
        return None
    ticker = m.group(1)
    if ticker not in TARGET_TICKERS:
        return None
    return {
        "symbol": symbol,
        "ticker": ticker,
        "expiry": m.group(2),
        "option_type": m.group(3),
        "strike": float(m.group(4)),
        "bid_price": None,
        "ask_price": None,
        "bid_size": None,
        "ask_size": None,
    }


def _option_contract_last_day_from_expiry_code(expiry_code: str):
    import calendar as _cal
    s = str(expiry_code or "").strip()
    m = re.match(r"^(\d{2})(\d{2})$", s)
    if not m:
        return None
    month = int(m.group(1))
    year = 2000 + int(m.group(2))
    if month < 1 or month > 12:
        return None
    overrides = {"0526": 25}
    day = overrides.get(s, _cal.monthrange(year, month)[1])
    return dt.datetime(year, month, day).date()


def _option_dtm_from_expiry_code(expiry_code: str, base_date=None):
    if base_date is None:
        base_date = dt.datetime.now().date()
    expiry_date = _option_contract_last_day_from_expiry_code(expiry_code)
    if expiry_date is None:
        return None
    return max(0, (expiry_date - base_date).days)


def _build_enriched_options(options_for_ticker, ticker, maturities, spot_mid, rates_by_ticker):
    enriched = []
    maturity_map = {}
    for m in maturities:
        code = m["code"]
        maturity_map[code] = {"futures_dtm": m["dtm"]}
    for code in maturity_map:
        if code in rates_by_ticker.get(ticker, {}):
            maturity_map[code]["rate"] = rates_by_ticker[ticker][code].get("rate")
        else:
            maturity_map[code]["rate"] = None
    spot = spot_mid.get(ticker)
    strikes_by_expiry = {}
    for _, opt_data in options_for_ticker.items():
        key = (opt_data["expiry"], opt_data["strike"])
        if key not in strikes_by_expiry:
            strikes_by_expiry[key] = {"C": None, "P": None}
        strikes_by_expiry[key][opt_data["option_type"]] = opt_data
    for (expiry, strike), options_dict in strikes_by_expiry.items():
        call_opt = options_dict.get("C")
        put_opt = options_dict.get("P")
        mat_info = maturity_map.get(expiry, {"futures_dtm": None, "rate": None})
        option_dtm = _option_dtm_from_expiry_code(expiry)
        rate = mat_info.get("rate")
        t = (option_dtm / 365.0) if option_dtm is not None and option_dtm > 0 else None
        call_bid_iv = call_ask_iv = put_bid_iv = put_ask_iv = None
        call_delta = put_delta = None
        if spot is not None and rate is not None and t is not None:
            if call_opt:
                cbp = call_opt.get("bid_price")
                cap = call_opt.get("ask_price")
                call_bid_iv = _implied_vol_bisect("C", cbp, spot, strike, rate, t) if cbp is not None else None
                call_ask_iv = _implied_vol_bisect("C", cap, spot, strike, rate, t) if cap is not None else None
                call_delta = _bs_delta("C", spot, strike, rate, t, _iv_mid(call_bid_iv, call_ask_iv))
            if put_opt:
                pbp = put_opt.get("bid_price")
                pap = put_opt.get("ask_price")
                put_bid_iv = _implied_vol_bisect("P", pbp, spot, strike, rate, t) if pbp is not None else None
                put_ask_iv = _implied_vol_bisect("P", pap, spot, strike, rate, t) if pap is not None else None
                put_delta = _bs_delta("P", spot, strike, rate, t, _iv_mid(put_bid_iv, put_ask_iv))
        enriched.append({
            "expiry": expiry, "strike": strike, "dtm": option_dtm, "rate": rate, "spot_mid": spot,
            "call_bid_size": call_opt.get("bid_size") if call_opt else None,
            "call_bid_price": call_opt.get("bid_price") if call_opt else None,
            "call_ask_price": call_opt.get("ask_price") if call_opt else None,
            "call_ask_size": call_opt.get("ask_size") if call_opt else None,
            "call_bid_iv": call_bid_iv, "call_ask_iv": call_ask_iv, "call_delta": call_delta,
            "put_delta": put_delta, "put_bid_iv": put_bid_iv, "put_ask_iv": put_ask_iv,
            "put_bid_size": put_opt.get("bid_size") if put_opt else None,
            "put_bid_price": put_opt.get("bid_price") if put_opt else None,
            "put_ask_price": put_opt.get("ask_price") if put_opt else None,
            "put_ask_size": put_opt.get("ask_size") if put_opt else None,
        })
    enriched.sort(key=lambda x: (x["expiry"], x["strike"]))
    return enriched


# --- Price source ---
#
# NOTE: the upstream ingestor (a separate service we don't control) has its
# "bid" and "ask" fields swapped at the source — verified against a raw
# feed sample where the same symbol's two partial quote updates carried
# tag 9 (higher price) and tag 6 (lower price); ask must be >= bid, so tag
# 9 is really the ask and tag 6 is really the bid, but the ingestor labels
# them the other way round. That's confirmed by ~60-99% of live records
# showing bid > ask across spot/futures/options, which is only possible if
# the labels are inverted. We correct for it here by reading the source's
# "ask" field as our bid and its "bid" field as our ask, rather than in the
# ingestor itself (no access to that code).
_SOURCE_BID_FIELD = "ask"
_SOURCE_ASK_FIELD = "bid"


def _clean_side_price(value):
    """The source uses a literal 0 to mean "no quote on this side" (a real
    market never has a $0.00 bid/ask); treat it the same as missing."""
    if value is None:
        return None
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None
    return fval if fval != 0.0 else None


def _record_bid_ask(rec):
    if not isinstance(rec, dict):
        return None, None
    bid = _clean_side_price(rec.get(_SOURCE_BID_FIELD))
    ask = _clean_side_price(rec.get(_SOURCE_ASK_FIELD))
    return bid, ask


def _record_mid(rec):
    bid, ask = _record_bid_ask(rec)
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return None


def fetch_snapshot(session, pool):
    """One full market snapshot: page through the entire price universe via
    limit/offset (source is Postgres-backed, ORDER BY symbol, so pages are
    stable across calls), then fall back to per-symbol lookups for any of
    our own tracked tickers that still didn't show up (defensive only —
    with offset paging this should normally be empty)."""
    by_symbol = {}
    offset = 0
    while True:
        resp = session.get(
            PRICES_LIST_ENDPOINT,
            params={"limit": PRICES_PAGE_LIMIT, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        page = [d for d in resp.json() if isinstance(d, dict)]
        for d in page:
            by_symbol[d.get("symbol")] = d
        if len(page) < PRICES_PAGE_LIMIT:
            break
        offset += PRICES_PAGE_LIMIT

    missing = [t for t in TARGET_TICKERS if t not in by_symbol]

    def _fetch_one(symbol):
        try:
            r = session.get(PRICES_SYMBOL_ENDPOINT.format(symbol=symbol), timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and not data.get("error"):
                    return symbol, data
        except requests.RequestException:
            pass
        return symbol, None

    if missing:
        for symbol, rec in pool.map(_fetch_one, missing):
            if rec is not None:
                by_symbol[symbol] = rec
    return by_symbol


# --- Frontend POST helpers (same contracts as main.ipynb Cell 4) ---

def post_spot_mid(session, ticker, spot_mid, ts):
    payload = {"ticker": ticker, "spot_mid": float(spot_mid), "ts": ts}
    r = session.post(FRONTEND_SPOT_ENDPOINT, json=payload, timeout=2)
    r.raise_for_status()


def post_futures_rates_batch(session, rates_by_ticker, maturities, ts):
    payload = {
        "rates_by_ticker": rates_by_ticker,
        "maturities": [{"code": m["code"], "label": m["label"], "dtm": m["dtm"]} for m in maturities],
        "ts": ts,
    }
    r = session.post(FRONTEND_FUTURES_RATES_ENDPOINT, json=payload, timeout=3)
    r.raise_for_status()


def post_options_chain(session, ticker, options, ts):
    payload = {"ticker": ticker, "options": options, "ts": ts}
    r = session.post(FRONTEND_OPTIONS_CHAIN_ENDPOINT, json=payload, timeout=3)
    r.raise_for_status()


# --- Main loop ---

def run_bridge():
    maturities, futures_symbol_map = build_futures_symbol_map()
    maturity_codes = {m["code"] for m in maturities}
    print("[INFO] HTTP bridge started.")
    print("[INFO] Source:", PRICES_LIST_ENDPOINT)
    print("[INFO] Frontend:", FRONTEND_BASE_URL)
    print("[INFO] Maturities:", [f"{m['label']}({m['code']}, DTM={m['dtm']})" for m in maturities])

    session = requests.Session()
    pool = ThreadPoolExecutor(max_workers=16)

    last_posted_spot = {}
    last_opt_sig = {}
    spot_posted = rates_posted = opts_posted = 0
    d_spot = d_rates = d_opts = 0
    last_log = time.time()
    last_err = "—"

    while True:
        cycle_start = time.time()
        try:
            by_symbol = fetch_snapshot(session, pool)
        except requests.RequestException as e:
            print(f"[WARN] snapshot fetch failed: {type(e).__name__}: {e}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

        spot_mid = {}
        spot_bid = {}
        spot_ask = {}
        for t in TARGET_TICKERS:
            bid, ask = _record_bid_ask(by_symbol.get(t))
            if bid is not None:
                spot_bid[t] = bid
            if ask is not None:
                spot_ask[t] = ask
            if bid is not None and ask is not None:
                spot_mid[t] = (bid + ask) / 2.0

        fut_mid = {}
        fut_bid = {}
        fut_ask = {}
        for fut_symbol in futures_symbol_map:
            bid, ask = _record_bid_ask(by_symbol.get(fut_symbol))
            if bid is not None:
                fut_bid[fut_symbol] = bid
            if ask is not None:
                fut_ask[fut_symbol] = ask
            if bid is not None and ask is not None:
                fut_mid[fut_symbol] = (bid + ask) / 2.0

        options_by_ticker = {}
        for symbol, rec in by_symbol.items():
            if not symbol or not symbol.startswith("O_"):
                continue
            parsed = _parse_option_symbol(symbol)
            if parsed is None or parsed["expiry"] not in maturity_codes:
                continue
            # See _SOURCE_BID_FIELD/_SOURCE_ASK_FIELD note above: the
            # source's fields are swapped, so we read them crossed, and a
            # literal 0 means "no quote" rather than a real price.
            parsed["bid_price"] = _clean_side_price(rec.get(_SOURCE_BID_FIELD))
            parsed["ask_price"] = _clean_side_price(rec.get(_SOURCE_ASK_FIELD))
            options_by_ticker.setdefault(parsed["ticker"], {})[symbol] = parsed

        # Spot: post only changed mids
        for t, mid in spot_mid.items():
            prev = last_posted_spot.get(t)
            if prev is not None and abs(mid - prev) <= 1e-12:
                continue
            try:
                post_spot_mid(session, t, mid, ts)
                last_posted_spot[t] = mid
                spot_posted += 1
                d_spot += 1
            except requests.RequestException as e:
                last_err = f"spot:{type(e).__name__}"

        # Futures rates: batch every cycle
        rates_by_ticker = _build_rates_snapshot(
            maturities, futures_symbol_map, spot_mid, fut_mid,
            spot_bid=spot_bid, spot_ask=spot_ask, fut_bid=fut_bid, fut_ask=fut_ask,
        )
        try:
            post_futures_rates_batch(session, rates_by_ticker, maturities, ts)
            rates_posted += 1
            d_rates += 1
        except requests.RequestException as e:
            last_err = f"rates:{type(e).__name__}"

        # Options: post per ticker only when its chain changed
        for ticker, opts in options_by_ticker.items():
            sig = hash(tuple(sorted(
                (s, o["bid_price"], o["ask_price"]) for s, o in opts.items()
            )))
            if last_opt_sig.get(ticker) == sig:
                continue
            enriched = _build_enriched_options(opts, ticker, maturities, spot_mid, rates_by_ticker)
            if not enriched:
                continue
            try:
                post_options_chain(session, ticker, enriched, ts)
                last_opt_sig[ticker] = sig
                opts_posted += 1
                d_opts += 1
            except requests.RequestException as e:
                last_err = f"opts:{type(e).__name__}"

        now = time.time()
        if now - last_log >= LOG_INTERVAL_SEC:
            total_opts = sum(len(v) for v in options_by_ticker.values())
            print(
                f"[{dt.datetime.now().strftime('%H:%M:%S')}] "
                f"ds={d_spot} dr={d_rates} do={d_opts} | "
                f"spot={len(spot_mid)}/{len(TARGET_TICKERS)} "
                f"fut={len(fut_mid)}/{len(futures_symbol_map)} "
                f"opt_syms={total_opts} | last_err={last_err}"
            )
            d_spot = d_rates = d_opts = 0
            last_log = now

        elapsed = time.time() - cycle_start
        time.sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    run_bridge()
