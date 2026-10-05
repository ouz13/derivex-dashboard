"""
Stream Bridge: IdealData TCP Stream -> Dashboard Frontend

Development-environment counterpart to bridge_http.py. Connects directly
to the IdealData TCP feed (the same feed the shared HTTP price cache is
built from) instead of polling a price-cache HTTP service, and POSTs the
same payload contracts to the frontend, so the frontend needs no changes.

    ssdata1.idealdata.com.tr:9443  ->  bridge_stream.py  ->  http://127.0.0.1:8000

Usage:
    python3 bridge_stream.py
"""

import math
import os
import re
import socket
import time
from datetime import datetime

import requests

def _env_yukle(dosya=".env"):
    """Script'in yanindaki .env dosyasini ortama yukler (mevcut degerleri ezmez)."""
    yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), dosya)
    if not os.path.isfile(yol):
        return
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir or satir.startswith("#") or "=" not in satir:
                continue
            k, v = satir.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_env_yukle()


# Kimlik bilgileri gomulu: kodu alan kisinin hicbir ayar yapmasi gerekmesin.
# Ortam degiskeni ya da .env varsa onlar onceliklidir.
STREAM_HOST = os.environ.get("STREAM_HOST", "ssdata1.idealdata.com.tr")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "9443"))
STREAM_USERNAME = os.environ.get("STREAM_USERNAME", "derivextestuser")
STREAM_PASSWORD = os.environ.get("STREAM_PASSWORD", "De12Ve1225")

HB_INTERVAL = 15.0
SOCK_TIMEOUT = 0.5
RUN_SECONDS = None  # run indefinitely, like bridge_http.py's poll loop

FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://127.0.0.1:5173")
FRONTEND_SPOT_ENDPOINT = f"{FRONTEND_BASE_URL}/api/spot"
FRONTEND_FUTURES_RATES_ENDPOINT = f"{FRONTEND_BASE_URL}/api/futures-rates"
FRONTEND_OPTIONS_CHAIN_ENDPOINT = f"{FRONTEND_BASE_URL}/api/options-chain"

TARGET_TICKERS = [
    "AEFES", "AKBNK", "AKSEN", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "CIMSA",
    "DOAS", "DOHOL", "EKGYO", "ENKAI", "ENJSA", "EREGL", "FROTO", "GARAN", "GUBRF", "HALKB",
    "HEKTS", "ISCTR", "KCHOL", "TRMET", "TRALT", "KRDMD", "MGROS", "ODAS", "OYAKC",
    "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "SOKM", "TAVHL", "TCELL", "THYAO", "TKFEN",
    "TOASO", "TSKB", "TTKOM", "TUPRS", "ULKER", "VAKBN", "VESTL", "YKBNK",
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


# --- Maturity / DTM helpers (same as bridge_http.py) ---

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
        d += __import__("datetime").timedelta(days=1)
        if is_working_day_tr(d):
            left -= 1
    return d


def last_business_day_of_month_tr(year, month):
    if month == 12:
        d = datetime(year + 1, 1, 1, 12, 0, 0) - __import__("datetime").timedelta(days=1)
    else:
        d = datetime(year, month + 1, 1, 12, 0, 0) - __import__("datetime").timedelta(days=1)
    while not is_working_day_tr(d):
        d -= __import__("datetime").timedelta(days=1)
    return d


def get_active_maturities(base_dt=None):
    if base_dt is None:
        base_dt = datetime.now()

    start_t2 = add_business_days_tr(base_dt, 2)
    out = []
    for offset in (0, 1, 2):
        month_index0 = base_dt.month - 1 + offset
        year = base_dt.year + (month_index0 // 12)
        month = (month_index0 % 12) + 1

        expiry = last_business_day_of_month_tr(year, month)
        expiry_t2 = add_business_days_tr(expiry, 2)
        dtm = max(0, (expiry_t2.date() - start_t2.date()).days)

        out.append({
            "year": year,
            "month": month,
            "code": f"{month:02d}{str(year)[-2:]}",
            "label": datetime(year, month, 1).strftime("%b"),
            "dtm": dtm,
        })
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


# --- Black-Scholes / IV helpers (same as bridge_http.py) ---

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


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_gamma(spot, strike, rate, t, sigma):
    """Spot'taki 1 birimlik degisime karsi delta degisimi. Call ve put icin ayni."""
    d1 = _bs_d1(spot, strike, rate, t, sigma)
    if d1 is None:
        return None
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(t))


def _bs_vega(spot, strike, rate, t, sigma):
    """Volatilitedeki 1 PUAN (%1) degisime karsi fiyat degisimi. Call/put ayni."""
    d1 = _bs_d1(spot, strike, rate, t, sigma)
    if d1 is None:
        return None
    return spot * _norm_pdf(d1) * math.sqrt(t) / 100.0


def _bs_theta(option_type, spot, strike, rate, t, sigma):
    """Zaman asinmasi, GUNLUK (yillik deger 365'e bolunur)."""
    d1 = _bs_d1(spot, strike, rate, t, sigma)
    if d1 is None:
        return None
    d2 = d1 - sigma * math.sqrt(t)
    df = math.exp(-rate * t)
    ortak = -(spot * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(t))
    if option_type == "C":
        yillik = ortak - rate * strike * df * _norm_cdf(d2)
    elif option_type == "P":
        yillik = ortak + rate * strike * df * _norm_cdf(-d2)
    else:
        return None
    return yillik / 365.0


def _bs_rho(option_type, spot, strike, rate, t, sigma):
    """Faizdeki 1 PUAN (%1) degisime karsi fiyat degisimi."""
    d1 = _bs_d1(spot, strike, rate, t, sigma)
    if d1 is None:
        return None
    d2 = d1 - sigma * math.sqrt(t)
    df = math.exp(-rate * t)
    if option_type == "C":
        return strike * t * df * _norm_cdf(d2) / 100.0
    if option_type == "P":
        return -strike * t * df * _norm_cdf(-d2) / 100.0
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
    return datetime(year, month, day).date()


def _option_dtm_from_expiry_code(expiry_code: str, base_date=None):
    if base_date is None:
        base_date = datetime.now().date()
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
        call_gamma = call_vega = call_theta = call_rho = None
        put_gamma = put_vega = put_theta = put_rho = None
        if spot is not None and rate is not None and t is not None:
            if call_opt:
                cbp = call_opt.get("bid_price")
                cap = call_opt.get("ask_price")
                call_bid_iv = _implied_vol_bisect("C", cbp, spot, strike, rate, t) if cbp is not None else None
                call_ask_iv = _implied_vol_bisect("C", cap, spot, strike, rate, t) if cap is not None else None
                civ = _iv_mid(call_bid_iv, call_ask_iv)
                call_delta = _bs_delta("C", spot, strike, rate, t, civ)
                call_gamma = _bs_gamma(spot, strike, rate, t, civ) if civ else None
                call_vega = _bs_vega(spot, strike, rate, t, civ) if civ else None
                call_theta = _bs_theta("C", spot, strike, rate, t, civ) if civ else None
                call_rho = _bs_rho("C", spot, strike, rate, t, civ) if civ else None
            if put_opt:
                pbp = put_opt.get("bid_price")
                pap = put_opt.get("ask_price")
                put_bid_iv = _implied_vol_bisect("P", pbp, spot, strike, rate, t) if pbp is not None else None
                put_ask_iv = _implied_vol_bisect("P", pap, spot, strike, rate, t) if pap is not None else None
                piv = _iv_mid(put_bid_iv, put_ask_iv)
                put_delta = _bs_delta("P", spot, strike, rate, t, piv)
                put_gamma = _bs_gamma(spot, strike, rate, t, piv) if piv else None
                put_vega = _bs_vega(spot, strike, rate, t, piv) if piv else None
                put_theta = _bs_theta("P", spot, strike, rate, t, piv) if piv else None
                put_rho = _bs_rho("P", spot, strike, rate, t, piv) if piv else None
        enriched.append({
            "expiry": expiry, "strike": strike, "dtm": option_dtm, "rate": rate, "spot_mid": spot,
            "call_bid_size": call_opt.get("bid_size") if call_opt else None,
            "call_bid_price": call_opt.get("bid_price") if call_opt else None,
            "call_ask_price": call_opt.get("ask_price") if call_opt else None,
            "call_ask_size": call_opt.get("ask_size") if call_opt else None,
            "call_bid_iv": call_bid_iv, "call_ask_iv": call_ask_iv, "call_delta": call_delta,
            "call_gamma": call_gamma, "call_vega": call_vega,
            "call_theta": call_theta, "call_rho": call_rho,
            "put_delta": put_delta, "put_bid_iv": put_bid_iv, "put_ask_iv": put_ask_iv,
            "put_gamma": put_gamma, "put_vega": put_vega,
            "put_theta": put_theta, "put_rho": put_rho,
            "put_bid_size": put_opt.get("bid_size") if put_opt else None,
            "put_bid_price": put_opt.get("bid_price") if put_opt else None,
            "put_ask_price": put_opt.get("ask_price") if put_opt else None,
            "put_ask_size": put_opt.get("ask_size") if put_opt else None,
        })
    enriched.sort(key=lambda x: (x["expiry"], x["strike"]))
    return enriched



def _mid_from_bid_ask(rec):
    if not rec:
        return None
    bid = rec.get("B")
    ask = rec.get("A")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _first_numeric(fields, keys):
    for k in keys:
        v = fields.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (ValueError, TypeError):
            continue
    return None



# --- Stream connection ---

def read_initial_response(sock: socket.socket, seconds: float = 2.0) -> str:
    buf = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            pass
    return buf.decode("utf-8", errors="replace")


def parse_frames(buffer: bytes):
    frames = []
    while b"|" in buffer:
        idx = buffer.index(b"|")
        raw = buffer[:idx].decode("utf-8", errors="replace").strip()
        buffer = buffer[idx + 1:]
        if raw:
            frames.append(raw)
    return frames, buffer


def parse_frame(raw: str):
    """'TIP;alan=deger;alan=deger' -> (tip, {alan: deger})."""
    parts = raw.split(";")
    if not parts:
        return None, {}
    out = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k] = v
    return parts[0], out


# Kotasyon tasiyan mesaj tipleri ve alan konumlari.
#   YU : tam/kismi anlik goruntu   -> 6/9 fiyat, 7/10 miktar
#   WU : yalnizca kotasyon         -> 108/109 fiyat, 110/111 miktar
#   DU : emir defteri guncellemesi -> 3 fiyat, 4 miktar, 101 taraf, 100 seviye
# Ucu de kismi guncellemedir: bir mesaj yalnizca degisen alanlari tasir, bu
# yuzden sembol basina birlestirilmis durum tutulur.
QUOTE_MSG_TYPES = ("YU", "WU", "DU")


def extract_quote(msg_type: str, fields: dict):
    """Mesajdan (bid, ask, bid_size, ask_size) cikarir; yoksa None birakir."""
    if msg_type == "WU":
        return (_first_numeric(fields, ["108"]), _first_numeric(fields, ["109"]),
                _first_numeric(fields, ["110"]), _first_numeric(fields, ["111"]))

    if msg_type == "YU":
        return (_first_numeric(fields, ["6"]), _first_numeric(fields, ["9"]),
                _first_numeric(fields, ["7"]), _first_numeric(fields, ["10"]))

    if msg_type == "DU":
        # Alan 100 derinlik seviyesidir (0 tabanli); yalnizca en iyi seviye.
        if fields.get("100") != "0":
            return None, None, None, None
        side = fields.get("101")
        px = _first_numeric(fields, ["3"])
        sz = _first_numeric(fields, ["4"])
        if side == "B":
            return px, None, sz, None
        if side == "A":
            return None, px, None, sz

    return None, None, None, None


def apply_quote(book: dict, symbol: str, bid, ask, bid_sz=None, ask_sz=None) -> bool:
    """Kotasyonu sembolun defterine isler. Fiyat degistiyse True doner."""
    rec = book.setdefault(symbol, {"B": None, "A": None, "BS": None, "AS": None})
    changed = False
    for deger, anahtar in ((bid, "B"), (ask, "A")):
        if deger is None:
            continue
        onceki = rec[anahtar]
        if onceki is None or abs(deger - onceki) > 1e-12:
            rec[anahtar] = deger
            changed = True
    for deger, anahtar in ((bid_sz, "BS"), (ask_sz, "AS")):
        if deger is not None:
            rec[anahtar] = int(deger) if deger >= 0 else None
    return changed


def connect_and_login(max_login_retries: int = 6, retry_wait_seconds: float = 5.0) -> socket.socket:
    last_error = None
    for attempt in range(1, max_login_retries + 1):
        sock = None
        try:
            print(f"[INFO] Connecting to {STREAM_HOST}:{STREAM_PORT} ... (attempt {attempt}/{max_login_retries})")
            sock = socket.create_connection((STREAM_HOST, STREAM_PORT), timeout=8)
            sock.settimeout(SOCK_TIMEOUT)

            login_msg = f"Login;{STREAM_USERNAME};{STREAM_PASSWORD}|"
            sock.sendall(login_msg.encode("utf-8"))
            print(f"[TX] {login_msg.strip()}")

            initial = read_initial_response(sock, seconds=2.0)
            if initial:
                low = initial.lower()
                already_connected = (
                    "bu kullanici zaten bagli" in low
                    or "already connected" in low
                )
                if already_connected:
                    sock.close()
                    sock = None
                    last_error = RuntimeError("IdealData login rejected: user already connected")
                    if attempt < max_login_retries:
                        print(f"[WARN] User already connected. Retrying in {retry_wait_seconds:.1f}s...")
                        time.sleep(retry_wait_seconds)
                        continue
                    raise RuntimeError(
                        "IdealData login rejected: user already connected after retries. "
                        "Close the other active session (if any) and retry."
                    )
            return sock
        except Exception as exc:
            last_error = exc
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            if attempt < max_login_retries:
                print(f"[WARN] Connection/login failed on attempt {attempt}: {exc}")
                print(f"[WARN] Retrying in {retry_wait_seconds:.1f}s...")
                time.sleep(retry_wait_seconds)
                continue
            raise
    raise RuntimeError(f"connect_and_login failed after retries: {last_error}")


# --- Frontend POST helpers (same contracts as bridge_http.py) ---

def post_spot_mid(ticker: str, spot_mid: float, ts: str):
    payload = {"ticker": ticker, "spot_mid": float(spot_mid), "ts": ts}
    r = requests.post(FRONTEND_SPOT_ENDPOINT, json=payload, timeout=2)
    r.raise_for_status()
    return r.json()


def post_futures_rates_batch(rates_by_ticker: dict, maturities: list, ts: str):
    payload = {
        "rates_by_ticker": rates_by_ticker,
        "maturities": [{"code": m["code"], "label": m["label"], "dtm": m["dtm"]} for m in maturities],
        "ts": ts,
    }
    r = requests.post(FRONTEND_FUTURES_RATES_ENDPOINT, json=payload, timeout=3)
    r.raise_for_status()
    return r.json()


def post_options_chain(ticker: str, options: list, ts: str):
    payload = {"ticker": ticker, "options": options, "ts": ts}
    r = requests.post(FRONTEND_OPTIONS_CHAIN_ENDPOINT, json=payload, timeout=3)
    r.raise_for_status()
    return r.json()


# --- Main loop ---

def run_combined_bridge(run_seconds=RUN_SECONDS, post_interval_sec=2.0):
    maturities, futures_symbol_map = build_futures_symbol_map()
    ticker_set = set(TARGET_TICKERS)
    futures_symbol_set = set(futures_symbol_map.keys())
    spot_book = {}
    fut_book = {}
    opt_book = {}
    options_by_ticker = {}
    spot_mid = {}
    fut_mid = {}
    spot_bid = {}
    spot_ask = {}
    fut_bid = {}
    fut_ask = {}
    last_posted_spot_mid = {}
    sock = connect_and_login()
    buffer = b""
    last_hb = 0.0
    started = time.time()
    spot_posted = 0
    rates_post_count = 0
    options_post_count = 0
    last_rates_post_ts = 0.0
    last_options_post_ts = 0.0
    price_changed = False
    option_changed_tickers = set()
    LOG_INTERVAL = 60.0
    last_log_ts = started
    delta_spot = 0
    delta_rates = 0
    delta_opts = 0
    last_fe_status = "—"

    print("[INFO] Stream bridge started.")
    print("[INFO] Source:", f"{STREAM_HOST}:{STREAM_PORT}")
    print("[INFO] Frontend:", FRONTEND_BASE_URL)
    print("[INFO] Maturities:", [f"{m['label']}({m['code']}, DTM={m['dtm']})" for m in maturities])

    try:
        while True:
            now = time.time()
            if now - last_hb >= HB_INTERVAL:
                sock.sendall(f"HB;{STREAM_USERNAME};{STREAM_PASSWORD}|".encode("utf-8"))
                last_hb = now
            if run_seconds is not None and (now - started) >= run_seconds:
                print(f"[INFO] run_seconds={run_seconds} reached.")
                break
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("[INFO] Server closed connection.")
                    break
                buffer += chunk
                frames, buffer = parse_frames(buffer)
                for raw in frames:
                    msg_type, fields = parse_frame(raw)
                    if msg_type not in QUOTE_MSG_TYPES:
                        continue
                    symbol = fields.get("1", "")
                    if not symbol:
                        continue

                    bid, ask, bid_sz, ask_sz = extract_quote(msg_type, fields)
                    if bid is None and ask is None:
                        continue

                    # --- Opsiyon ---
                    if symbol.startswith("O_"):
                        parsed = _parse_option_symbol(symbol)
                        if parsed is None:
                            continue
                        ticker = parsed["ticker"]
                        ticker_options = options_by_ticker.setdefault(ticker, {})
                        if symbol not in ticker_options:
                            ticker_options[symbol] = parsed
                            option_changed_tickers.add(ticker)
                        if apply_quote(opt_book, symbol, bid, ask, bid_sz, ask_sz):
                            option_changed_tickers.add(ticker)
                        rec = opt_book[symbol]
                        satir = ticker_options[symbol]
                        satir["bid_price"] = rec["B"]
                        satir["ask_price"] = rec["A"]
                        satir["bid_size"] = rec["BS"]
                        satir["ask_size"] = rec["AS"]
                        continue

                    # --- Spot ---
                    if symbol in ticker_set:
                        if not apply_quote(spot_book, symbol, bid, ask, bid_sz, ask_sz):
                            continue
                        rec = spot_book[symbol]
                        smid = _mid_from_bid_ask(rec)
                        if smid is None:
                            continue
                        spot_bid[symbol] = rec["B"]
                        spot_ask[symbol] = rec["A"]
                        spot_mid[symbol] = smid
                        price_changed = True
                        onceki = last_posted_spot_mid.get(symbol)
                        if onceki is None or abs(smid - onceki) > 1e-12:
                            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            try:
                                post_spot_mid(symbol, smid, ts)
                                last_posted_spot_mid[symbol] = smid
                                spot_posted += 1
                                delta_spot += 1
                                last_fe_status = "OK"
                            except Exception as e:
                                last_fe_status = f"ERR:{type(e).__name__}"
                        continue

                    # --- Vadeli ---
                    if symbol in futures_symbol_set:
                        if not apply_quote(fut_book, symbol, bid, ask, bid_sz, ask_sz):
                            continue
                        rec = fut_book[symbol]
                        fmid = _mid_from_bid_ask(rec)
                        if fmid is None:
                            continue
                        fut_bid[symbol] = rec["B"]
                        fut_ask[symbol] = rec["A"]
                        fut_mid[symbol] = fmid
                        price_changed = True
            except socket.timeout:
                pass

            if price_changed or (now - last_rates_post_ts >= post_interval_sec):
                rates_by_ticker = _build_rates_snapshot(
                    maturities, futures_symbol_map, spot_mid, fut_mid,
                    spot_bid=spot_bid, spot_ask=spot_ask, fut_bid=fut_bid, fut_ask=fut_ask,
                )
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                try:
                    post_futures_rates_batch(rates_by_ticker, maturities, ts)
                    rates_post_count += 1
                    delta_rates += 1
                    last_fe_status = "OK"
                except Exception as e:
                    last_fe_status = f"ERR:{type(e).__name__}"
                last_rates_post_ts = now
                price_changed = False

            if option_changed_tickers or (now - last_options_post_ts >= post_interval_sec):
                rates_by_ticker = _build_rates_snapshot(
                    maturities, futures_symbol_map, spot_mid, fut_mid,
                    spot_bid=spot_bid, spot_ask=spot_ask, fut_bid=fut_bid, fut_ask=fut_ask,
                )
                tickers_to_post = sorted(option_changed_tickers) if option_changed_tickers else sorted(
                    t for t, opts in options_by_ticker.items() if opts
                )
                for ticker in tickers_to_post:
                    opts_enriched = _build_enriched_options(
                        options_by_ticker.get(ticker, {}), ticker, maturities, spot_mid, rates_by_ticker
                    )
                    if opts_enriched:
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        try:
                            post_options_chain(ticker, opts_enriched, ts)
                            options_post_count += 1
                            delta_opts += 1
                            last_fe_status = "OK"
                        except Exception as e:
                            last_fe_status = f"ERR:{type(e).__name__}"
                last_options_post_ts = now
                option_changed_tickers.clear()

            if now - last_log_ts >= LOG_INTERVAL:
                total_opt_syms = sum(len(v) for v in options_by_ticker.values())
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"ds={delta_spot}  dr={delta_rates}  do={delta_opts}  "
                    f"s={len(last_posted_spot_mid)}/{len(TARGET_TICKERS)}  "
                    f"o={total_opt_syms}  fe={last_fe_status}"
                )
                delta_spot = delta_rates = delta_opts = 0
                last_log_ts = now
    except KeyboardInterrupt:
        print("\n[STOPPED]")
    finally:
        sock.close()
        print("[INFO] Socket closed.")

    rates_by_ticker = _build_rates_snapshot(
        maturities, futures_symbol_map, spot_mid, fut_mid,
        spot_bid=spot_bid, spot_ask=spot_ask, fut_bid=fut_bid, fut_ask=fut_ask,
    )
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        post_futures_rates_batch(rates_by_ticker, maturities, ts)
    except Exception:
        pass
    for ticker in sorted(t for t, opts in options_by_ticker.items() if opts):
        opts_enriched = _build_enriched_options(
            options_by_ticker.get(ticker, {}), ticker, maturities, spot_mid, rates_by_ticker
        )
        if opts_enriched:
            try:
                post_options_chain(ticker, opts_enriched, datetime.now().strftime("%H:%M:%S.%f")[:-3])
            except Exception:
                pass

    print("[SUMMARY]")
    print(f"spot_posts={spot_posted}  rates_posts={rates_post_count}  opts_posts={options_post_count}")


if __name__ == "__main__":
    run_combined_bridge(run_seconds=RUN_SECONDS)
