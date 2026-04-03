"""마스터 백테스트 — 데이터 1회 로드 후 모든 전략 순차 실행"""

import logging
import sys
import time
from collector.market_data import get_kospi200_symbols, get_kosdaq150_symbols, get_daily_ohlcv
from collector.us_market_data import bulk_download
from strategy.indicators import sma, ema, rsi, adx, atr, bollinger_bands

logger = logging.getLogger(__name__)

START = "20250201"
END = "20260331"
KR_CAPITAL = 700_000
US_CAPITAL = 300_000
KR_COMMISSION = 0.31  # 왕복 %
US_COMMISSION = 0.70  # 왕복 %
EXCHANGE_RATE = 1350

US_SYMBOLS = ["COIN", "MARA", "RIOT", "MSTR", "ETHE", "BITO",
              "HOOD", "BITF", "CLSK", "HUT", "CORZ", "IREN"]


# ─── 공통 유틸 ────────────────────────────────────────────
def calc_result(trades, daily_equity, init_capital):
    if not trades:
        return {"거래": 0, "승률": 0, "수익률": 0, "총손익": 0, "MDD": 0, "PF": 0}
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    total_pnl = sum(trades)
    win_rate = len(wins) / len(trades) * 100
    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1
    pf = gross_win / gross_loss

    peak = init_capital
    max_dd = 0.0
    for eq in daily_equity:
        peak = max(peak, eq)
        dd = (eq - peak) / peak * 100
        max_dd = min(max_dd, dd)

    final_eq = daily_equity[-1] if daily_equity else init_capital
    ret = (final_eq - init_capital) / init_capital * 100

    return {"거래": len(trades), "승률": round(win_rate, 1), "수익률": round(ret, 1),
            "총손익": int(total_pnl), "MDD": round(max_dd, 1), "PF": round(pf, 2)}


# ─── 전략 1: EMA 크로스 + RSI ─────────────────────────────
def strategy_ema_cross(all_data, all_dates, ema_short=9, ema_long=21, rsi_thresh=50,
                       stop=-3.0, target=5.0, max_hold=10):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]:
                continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0

            es = ema(data["closes"][:idx+1], ema_short)
            el = ema(data["closes"][:idx+1], ema_long)
            cross_down = idx > 0 and es[idx] < el[idx] and es[idx-1] >= el[idx-1]

            reason = None
            if pnl_pct <= stop: reason = 1
            elif pnl_pct >= target: reason = 1
            elif hold >= max_hold: reason = 1
            elif cross_down: reason = 1

            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                pnl = rev - cost - comm
                capital += cost + pnl
                trades_pnl.append(pnl)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5:
                    break
                if date not in data["dates"] or any(p["s"] == sym for p in positions):
                    continue
                idx = data["dates"].index(date)
                if idx < ema_long + 1:
                    continue
                closes = data["closes"]
                if closes[idx] < 5000:
                    continue
                es = ema(closes[:idx+1], ema_short)
                el = ema(closes[:idx+1], ema_long)
                if es[idx] <= el[idx] or (idx > 0 and es[idx-1] > el[idx-1]):
                    continue
                rv = rsi(closes[:idx+1], 14)
                if rv[idx] <= rsi_thresh:
                    continue
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 2: 스토캐스틱 ──────────────────────────────────
def stochastic(highs, lows, closes, k_period, d_period):
    n = len(closes)
    k_vals = [50.0] * n
    d_vals = [50.0] * n
    for i in range(k_period - 1, n):
        h = max(highs[i-k_period+1:i+1])
        l = min(lows[i-k_period+1:i+1])
        k_vals[i] = (closes[i] - l) / (h - l) * 100 if h != l else 50.0
    for i in range(k_period + d_period - 2, n):
        d_vals[i] = sum(k_vals[i-d_period+1:i+1]) / d_period
    return k_vals, d_vals


def strategy_stochastic(all_data, all_dates, k_per=9, d_per=3, oversold=20,
                        stop=-3.0, target=4.0, max_hold=7):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0

            k, d = stochastic(data["highs"][:idx+1], data["lows"][:idx+1], data["closes"][:idx+1], k_per, d_per)
            overbought = idx > 0 and k[idx-1] >= 80 and k[idx] < d[idx] and k[idx-1] >= d[idx-1]

            reason = None
            if pnl_pct <= stop: reason = 1
            elif pnl_pct >= target: reason = 1
            elif hold >= max_hold: reason = 1
            elif overbought: reason = 1

            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                pnl = rev - cost - comm
                capital += cost + pnl
                trades_pnl.append(pnl)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < k_per + d_per: continue
                if data["closes"][idx] < 5000: continue

                k, d = stochastic(data["highs"][:idx+1], data["lows"][:idx+1], data["closes"][:idx+1], k_per, d_per)
                if not (k[idx] <= oversold and idx > 0 and k[idx] > d[idx] and k[idx-1] <= d[idx-1]):
                    continue

                budget = capital // 5
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital -= data["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": data["closes"][idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 3: ORB (전일 고가 돌파) ─────────────────────────
def strategy_orb(all_data, all_dates, ma_period=20, vol_mult=1.5, target=5.0,
                 stop=-3.0, max_hold=5):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            reason = None
            if c < pos.get("sl", 0): reason = 1
            elif pnl_pct <= stop: reason = 1
            elif pnl_pct >= target: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                min_idx = max(50, ma_period) if ma_period else 50
                if idx < min_idx + 1: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                if closes[idx] <= data["highs"][idx-1]: continue
                avg_vol = sum(data["volumes"][max(0,idx-20):idx]) / min(20, idx) if idx > 0 else 1
                if avg_vol <= 0 or data["volumes"][idx] < avg_vol * vol_mult: continue
                if ma_period:
                    ma = sma(closes[:idx+1], ma_period)
                    if ma[idx] == 0 or closes[idx] <= ma[idx]: continue
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date,
                                  "sl": data["lows"][idx-1]})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 4: 피봇포인트 ──────────────────────────────────
def strategy_pivot(all_data, all_dates, touch_pct=1.0, exit_target="R1",
                   stop=-2.0, max_hold=5):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            reason = None
            if c >= pos.get("tp", float("inf")): reason = 1
            elif pnl_pct <= stop: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 2: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                ph, pl, pc = data["highs"][idx-1], data["lows"][idx-1], closes[idx-1]
                pivot = (ph + pl + pc) / 3
                s1 = 2 * pivot - ph
                r1 = 2 * pivot - pl
                tr = s1 * touch_pct / 100
                if not (abs(data["lows"][idx] - s1) <= tr or (data["lows"][idx] <= s1 and closes[idx] > s1)):
                    continue
                if closes[idx] <= data["opens"][idx]: continue
                tp = r1 if exit_target == "R1" else pivot
                if tp <= closes[idx]: continue
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date, "tp": tp})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 5: 삼중필터 (RSI+MACD+거래량) ──────────────────
def strategy_triple(all_data, all_dates, rsi_thresh=35, vol_mult=1.5,
                    stop=-3.0, max_hold=10):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            rv = rsi(data["closes"][:idx+1], 14)
            ef = ema(data["closes"][:idx+1], 12)
            es2 = ema(data["closes"][:idx+1], 26)
            macd_line = [ef[i] - es2[i] for i in range(len(ef))]
            sig = ema(macd_line, 9)
            hist = [macd_line[i] - sig[i] for i in range(len(macd_line))]

            reason = None
            if pnl_pct <= stop: reason = 1
            elif rv[idx] > 70: reason = 1
            elif idx > 0 and hist[idx] < 0 and hist[idx-1] >= 0: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 27: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                rv = rsi(closes[:idx+1], 14)
                if not (idx > 0 and rv[idx-1] < rsi_thresh and rv[idx] >= rsi_thresh): continue
                ef = ema(closes[:idx+1], 12)
                es2 = ema(closes[:idx+1], 26)
                macd_line = [ef[i] - es2[i] for i in range(len(ef))]
                sig = ema(macd_line, 9)
                hist = [macd_line[i] - sig[i] for i in range(len(macd_line))]
                if not (hist[idx-1] < 0 and hist[idx] >= 0): continue
                avg_vol = sum(data["volumes"][max(0,idx-20):idx]) / min(20, idx) if idx > 0 else 1
                if avg_vol <= 0 or data["volumes"][idx] < avg_vol * vol_mult: continue
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 6: ADX 방향성 + ATR 동적손절 ───────────────────
def adx_di(highs, lows, closes, period=14):
    n = len(closes)
    r_adx = [0.0]*n; r_pdi = [0.0]*n; r_mdi = [0.0]*n
    if n < period * 2: return r_adx, r_pdi, r_mdi
    tr_l = [0.0]*n; pdm = [0.0]*n; mdm = [0.0]*n
    for i in range(1, n):
        hd = highs[i]-highs[i-1]; ld = lows[i-1]-lows[i]
        tr_l[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        pdm[i] = hd if hd > ld and hd > 0 else 0
        mdm[i] = ld if ld > hd and ld > 0 else 0
    atr_v = sum(tr_l[1:period+1])/period
    pds = sum(pdm[1:period+1])/period
    mds = sum(mdm[1:period+1])/period
    dx_l = []
    for i in range(period+1, n):
        atr_v = (atr_v*(period-1)+tr_l[i])/period
        pds = (pds*(period-1)+pdm[i])/period
        mds = (mds*(period-1)+mdm[i])/period
        if atr_v == 0: continue
        pdi = 100*pds/atr_v; mdi = 100*mds/atr_v
        r_pdi[i] = pdi; r_mdi[i] = mdi
        ds = pdi+mdi
        dx = 100*abs(pdi-mdi)/ds if ds > 0 else 0
        dx_l.append((i, dx))
    if len(dx_l) < period: return r_adx, r_pdi, r_mdi
    av = sum(d for _,d in dx_l[:period])/period
    for j in range(period, len(dx_l)):
        idx, dv = dx_l[j]
        av = (av*(period-1)+dv)/period
        r_adx[idx] = av
    return r_adx, r_pdi, r_mdi


def strategy_adx(all_data, all_dates, adx_thresh=25, atr_sl=2.0, atr_tp=3.0, max_hold=10):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            av, pdi, mdi = adx_di(data["highs"][:idx+1], data["lows"][:idx+1], data["closes"][:idx+1])
            reason = None
            if c <= pos.get("sl", 0): reason = 1
            elif c >= pos.get("tp", float("inf")): reason = 1
            elif mdi[idx] > pdi[idx]: reason = 1
            elif av[idx] > 0 and av[idx] < 20: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 30: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                av, pdi, mdi = adx_di(data["highs"][:idx+1], data["lows"][:idx+1], closes[:idx+1])
                if av[idx] < adx_thresh or pdi[idx] <= mdi[idx]: continue
                if idx < 1 or pdi[idx] <= pdi[idx-1]: continue
                atr_v = atr(data["highs"][:idx+1], data["lows"][:idx+1], closes[:idx+1])
                a = atr_v[idx] if atr_v[idx] > 0 else closes[idx] * 0.02
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date,
                                  "sl": closes[idx] - atr_sl * a, "tp": closes[idx] + atr_tp * a})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 7: 볼린저 하단 반등 ─────────────────────────────
def strategy_bollinger(all_data, all_dates, bb_std=2.0, rsi_thresh=30,
                       stop=-3.0, max_hold=7):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            _, mid, _ = bollinger_bands(data["closes"][:idx+1], 20, bb_std)
            reason = None
            if pnl_pct <= stop: reason = 1
            elif mid[idx] > 0 and c >= mid[idx]: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 21: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                _, _, lower = bollinger_bands(closes[:idx+1], 20, bb_std)
                if lower[idx] == 0 or closes[idx] > lower[idx]: continue
                rv = rsi(closes[:idx+1], 14)
                if rv[idx] > rsi_thresh: continue
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 8: 급락 반등 (3일 연속 하락) ────────────────────
def strategy_reversal(all_data, all_dates, consec=3, bounce_pct=1.0,
                      stop=-5.0, max_hold=7):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            ma20 = sum(data["closes"][max(0,idx-19):idx+1]) / min(20, idx+1)
            reason = None
            if pnl_pct <= stop: reason = 1
            elif c >= ma20 and pnl_pct > 0: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            for sym, data in all_data.items():
                if len(positions) >= 5: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < consec + 1: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                # N일 연속 하락 후 양봉 반등
                all_down = all(closes[idx-i-1] > closes[idx-i] for i in range(consec))
                if not all_down: continue
                if closes[idx] <= data["opens"][idx]: continue  # 양봉
                drop = (closes[idx-consec] - closes[idx-1]) / closes[idx-consec] * 100
                if drop > -bounce_pct: continue
                budget = capital // 5
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 9: 변동성 돌파 (래리 윌리엄스) ──────────────────
def strategy_volatility(all_data, all_dates, k=0.5, use_ma=True, max_hold=1):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    trades_pnl = []
    equity = []

    for i, date in enumerate(dates):
        day_pnl = 0
        for sym, data in all_data.items():
            if date not in data["dates"]: continue
            idx = data["dates"].index(date)
            if idx < 21: continue
            closes = data["closes"]
            opens = data["opens"]
            highs = data["highs"]
            lows = data["lows"]
            if closes[idx] < 5000: continue

            prev_range = highs[idx-1] - lows[idx-1]
            target_price = opens[idx] + prev_range * k
            if highs[idx] < target_price: continue  # 돌파 안 됨

            if use_ma:
                ma5 = sum(closes[idx-4:idx+1]) / 5 if idx >= 4 else closes[idx]
                if opens[idx] < ma5: continue

            # 돌파 시 매수, 종가 청산
            entry = target_price
            if entry <= 0: continue
            exit_p = closes[idx]
            budget = capital // 10
            qty = int(budget / entry)
            if qty <= 0: continue
            cost = entry * qty
            rev = exit_p * qty
            comm = (cost + rev) * KR_COMMISSION / 100
            pnl = rev - cost - comm
            day_pnl += pnl
            trades_pnl.append(pnl)

        equity.append(capital + day_pnl)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 10: ATR 과매도 반등 ─────────────────────────────
def strategy_atr_oversold(all_data, all_dates, atr_mult=2.0, stop=-5.0, max_hold=7):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            ma20 = sum(data["closes"][max(0,idx-19):idx+1]) / min(20, idx+1)
            reason = None
            if pnl_pct <= stop: reason = 1
            elif c >= ma20 and pnl_pct > 0: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 2:
            for sym, data in all_data.items():
                if len(positions) >= 2: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 30: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                ma20 = sum(closes[idx-19:idx+1]) / 20
                atr_v = atr(data["highs"][:idx+1], data["lows"][:idx+1], closes[:idx+1])
                if atr_v[idx] <= 0: continue
                if closes[idx] > ma20 - atr_mult * atr_v[idx]: continue
                budget = capital // 2
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 11: RSI(2) Connors 단기 평균회귀 ──────────────────
def strategy_rsi2_connors(all_data, all_dates, rsi_period=2, rsi_entry=10,
                          rsi_exit=70, ma_period=200, stop=-5.0):
    """RSI(2)가 극단적 과매도일 때 매수, 반등 시 매도 (Larry Connors 스타일)"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            rv = rsi(data["closes"][:idx+1], rsi_period)
            reason = None
            if pnl_pct <= stop: reason = 1
            elif rv[idx] >= rsi_exit: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 5:
            candidates = []
            for sym, data in all_data.items():
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < max(ma_period, 20): continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                ma_v = sma(closes[:idx+1], ma_period)
                if ma_v[idx] <= 0 or closes[idx] < ma_v[idx]: continue  # 장기 상승추세만
                rv = rsi(closes[:idx+1], rsi_period)
                if rv[idx] < rsi_entry:
                    candidates.append((sym, rv[idx], idx))
            candidates.sort(key=lambda x: x[1])  # RSI 낮은 순
            for sym, _, idx in candidates[:5 - len(positions)]:
                data = all_data[sym]
                budget = capital // 5
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital -= data["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": data["closes"][idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 12: 듀얼 모멘텀 (절대+상대) ──────────────────────
def strategy_dual_momentum(all_data, all_dates, lookback=20, top_n=5,
                           rebal_days=5, stop=-7.0):
    """절대 모멘텀(양수 수익) + 상대 모멘텀(상위 N종목) 조합"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = {}
    trades_pnl = []
    equity = []
    day_count = 0

    for date in dates:
        day_count += 1

        # 손절 체크
        for sym in list(positions.keys()):
            data = all_data.get(sym)
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            pos = positions[sym]
            pnl_pct = (data["closes"][idx] - pos["ep"]) / pos["ep"] * 100
            if pnl_pct <= stop:
                cost = pos["ep"] * pos["q"]
                rev = data["closes"][idx] * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

        # 리밸런싱
        if day_count % rebal_days == 1:
            # 모멘텀 스코어 계산
            scores = []
            for sym, data in all_data.items():
                if date not in data["dates"]: continue
                idx = data["dates"].index(date)
                if idx < lookback: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                ret = (closes[idx] - closes[idx - lookback]) / closes[idx - lookback] * 100
                if ret <= 0: continue  # 절대 모멘텀 필터
                scores.append((sym, ret))
            scores.sort(key=lambda x: x[1], reverse=True)
            target_syms = set(s for s, _ in scores[:top_n])

            # 보유 중인데 상위에서 빠진 종목 매도
            for sym in list(positions.keys()):
                if sym in target_syms: continue
                data = all_data.get(sym)
                if not data or date not in data["dates"]: continue
                idx = data["dates"].index(date)
                pos = positions[sym]
                cost = pos["ep"] * pos["q"]
                rev = data["closes"][idx] * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

            # 신규 매수
            for sym in target_syms:
                if sym in positions: continue
                data = all_data.get(sym)
                if not data or date not in data["dates"]: continue
                idx = data["dates"].index(date)
                budget = capital // max(1, top_n - len(positions))
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital -= data["closes"][idx] * qty
                positions[sym] = {"q": qty, "ep": data["closes"][idx], "ed": date}

        eq = capital + sum(
            all_data[s]["closes"][all_data[s]["dates"].index(date)] * p["q"]
            if date in all_data[s]["dates"] else p["ep"] * p["q"]
            for s, p in positions.items())
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 13: ADX + EMA + RSI 콤보 ─────────────────────────
def strategy_combo_adx_ema_rsi(all_data, all_dates, adx_thresh=25, ema_short=13,
                                ema_long=21, rsi_low=40, rsi_high=70,
                                atr_sl=2.0, atr_tp=4.0, max_hold=15):
    """상위 전략들의 시그널을 결합: ADX 추세확인 + EMA 크로스 진입 + RSI 필터"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            atr_v = atr(data["highs"][:idx+1], data["lows"][:idx+1], data["closes"][:idx+1])
            sl_pct = -(atr_sl * atr_v[idx] / pos["ep"] * 100) if atr_v[idx] > 0 else -3.0
            tp_pct = atr_tp * atr_v[idx] / pos["ep"] * 100 if atr_v[idx] > 0 else 6.0
            reason = None
            if pnl_pct <= sl_pct: reason = 1
            elif pnl_pct >= tp_pct: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            candidates = []
            for sym, data in all_data.items():
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 60: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                adx_v = adx(data["highs"][:idx+1], data["lows"][:idx+1], closes[:idx+1])
                if adx_v[idx] < adx_thresh: continue
                es = ema(closes[:idx+1], ema_short)
                el = ema(closes[:idx+1], ema_long)
                if idx < 1 or not (es[idx] > el[idx] and es[idx-1] <= el[idx-1]): continue
                rv = rsi(closes[:idx+1], 14)
                if rv[idx] < rsi_low or rv[idx] > rsi_high: continue
                candidates.append((sym, adx_v[idx], idx))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for sym, _, idx in candidates[:3 - len(positions)]:
                data = all_data[sym]
                budget = capital // 3
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital -= data["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": data["closes"][idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 14: MACD 다이버전스 + 거래량 ─────────────────────
def strategy_macd_volume(all_data, all_dates, fast=12, slow=26, signal_p=9,
                         vol_mult=1.5, stop=-4.0, target=8.0, max_hold=12):
    """MACD 골든크로스 + 거래량 확인"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            macd_line = ema(data["closes"][:idx+1], fast)
            macd_slow = ema(data["closes"][:idx+1], slow)
            macd = [macd_line[i] - macd_slow[i] for i in range(len(macd_line))]
            sig = ema(macd, signal_p)
            dead_cross = idx > 0 and macd[idx] < sig[idx] and macd[idx-1] >= sig[idx-1]
            reason = None
            if pnl_pct <= stop: reason = 1
            elif pnl_pct >= target: reason = 1
            elif hold >= max_hold: reason = 1
            elif dead_cross and pnl_pct > 0: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, data in all_data.items():
                if len(positions) >= 3: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < slow + signal_p + 5: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                macd_line = ema(closes[:idx+1], fast)
                macd_slow_v = ema(closes[:idx+1], slow)
                macd = [macd_line[i] - macd_slow_v[i] for i in range(len(macd_line))]
                sig = ema(macd, signal_p)
                if not (macd[idx] > sig[idx] and macd[idx-1] <= sig[idx-1]): continue
                avg_vol = sum(data["volumes"][max(0,idx-20):idx]) / min(20, idx) if idx > 0 else 1
                if avg_vol <= 0 or data["volumes"][idx] < avg_vol * vol_mult: continue
                budget = capital // 3
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 15: 켈트너 채널 스퀴즈 브레이크아웃 ────────────────
def strategy_keltner_squeeze(all_data, all_dates, bb_period=20, bb_std=2.0,
                              kc_mult=1.5, atr_sl=2.0, atr_tp=3.5, max_hold=10):
    """볼린저밴드가 켈트너채널 안으로 수축 후 돌파 시 진입 (TTM Squeeze)"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            atr_v = atr(data["highs"][:idx+1], data["lows"][:idx+1], data["closes"][:idx+1])
            sl_pct = -(atr_sl * atr_v[idx] / pos["ep"] * 100) if pos["ep"] > 0 and atr_v[idx] > 0 else -3.0
            tp_pct = atr_tp * atr_v[idx] / pos["ep"] * 100 if pos["ep"] > 0 and atr_v[idx] > 0 else 7.0
            reason = None
            if pnl_pct <= sl_pct: reason = 1
            elif pnl_pct >= tp_pct: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, data in all_data.items():
                if len(positions) >= 3: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < bb_period + 5: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                bb_u, bb_m, bb_l = bollinger_bands(closes[:idx+1], bb_period, bb_std)
                atr_v = atr(data["highs"][:idx+1], data["lows"][:idx+1], closes[:idx+1])
                if atr_v[idx] <= 0 or bb_m[idx] <= 0: continue
                kc_upper = bb_m[idx] + kc_mult * atr_v[idx]
                kc_lower = bb_m[idx] - kc_mult * atr_v[idx]
                # 스퀴즈: BB가 KC 안에 있다가 밖으로 나감
                prev_squeeze = (idx > 0 and bb_u[idx-1] < kc_upper and bb_l[idx-1] > kc_lower)
                curr_break = bb_u[idx] >= kc_upper
                if not (prev_squeeze and curr_break): continue
                if closes[idx] <= bb_m[idx]: continue  # 상방 돌파만
                budget = capital // 3
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 16: 도치안 채널 돌파 (터틀) ─────────────────────────
def strategy_donchian(all_data, all_dates, entry_period=20, exit_period=10,
                      atr_sl=2.0, max_hold=20):
    """도치안 채널 상단 돌파 매수, 하단 이탈 매도 (터틀 트레이딩)"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = all_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            exit_low = min(data["lows"][max(0,idx-exit_period):idx]) if idx > 0 else data["lows"][idx]
            reason = None
            if c <= exit_low: reason = 1
            elif pnl_pct <= -(atr_sl * 3): reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, data in all_data.items():
                if len(positions) >= 3: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < entry_period + 5: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                entry_high = max(data["highs"][idx-entry_period:idx])
                if closes[idx] <= entry_high: continue  # 채널 상단 돌파
                adx_v = adx(data["highs"][:idx+1], data["lows"][:idx+1], closes[:idx+1])
                if adx_v[idx] < 20: continue  # 추세 확인
                budget = capital // 3
                qty = int(budget / closes[idx])
                if qty <= 0: continue
                capital -= closes[idx] * qty
                positions.append({"s": sym, "q": qty, "ep": closes[idx], "ed": date})

        eq = capital + sum(
            all_data[p["s"]]["closes"][all_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in all_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── 전략 17: 상대강도 로테이션 ─────────────────────────────
def strategy_relative_strength(all_data, all_dates, lookback=10, top_n=3,
                                rebal_days=5, min_ret=0, stop=-5.0):
    """N일 수익률 상위 종목만 집중 매수, 주기적 교체"""
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = {}
    trades_pnl = []
    equity = []
    day_count = 0

    for date in dates:
        day_count += 1
        for sym in list(positions.keys()):
            data = all_data.get(sym)
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            pos = positions[sym]
            pnl_pct = (data["closes"][idx] - pos["ep"]) / pos["ep"] * 100
            if pnl_pct <= stop:
                cost = pos["ep"] * pos["q"]
                rev = data["closes"][idx] * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

        if day_count % rebal_days == 1:
            scores = []
            for sym, data in all_data.items():
                if date not in data["dates"]: continue
                idx = data["dates"].index(date)
                if idx < lookback: continue
                closes = data["closes"]
                if closes[idx] < 5000: continue
                ret = (closes[idx] - closes[idx - lookback]) / closes[idx - lookback] * 100
                if ret <= min_ret: continue
                scores.append((sym, ret))
            scores.sort(key=lambda x: x[1], reverse=True)
            target_syms = set(s for s, _ in scores[:top_n])

            for sym in list(positions.keys()):
                if sym in target_syms: continue
                data = all_data.get(sym)
                if not data or date not in data["dates"]: continue
                idx = data["dates"].index(date)
                pos = positions[sym]
                cost = pos["ep"] * pos["q"]
                rev = data["closes"][idx] * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

            for sym in target_syms:
                if sym in positions: continue
                data = all_data.get(sym)
                if not data or date not in data["dates"]: continue
                idx = data["dates"].index(date)
                budget = capital // max(1, top_n - len(positions))
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital -= data["closes"][idx] * qty
                positions[sym] = {"q": qty, "ep": data["closes"][idx], "ed": date}

        eq = capital + sum(
            all_data[s]["closes"][all_data[s]["dates"].index(date)] * p["q"]
            if date in all_data[s]["dates"] else p["ep"] * p["q"]
            for s, p in positions.items())
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ─── US 전략: Mean Reversion ──────────────────────────────
def strategy_us_mean_rev(us_data, sma_per=20, deviation=5.0, rsi_thresh=30,
                         stop=-5.0, max_hold=10):
    all_dates = set()
    for d in us_data.values(): all_dates.update(d["dates"])
    dates = sorted(d for d in all_dates if START <= d <= END)
    capital_usd = US_CAPITAL / EXCHANGE_RATE
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = us_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            sma_v = sma(data["closes"][:idx+1], sma_per)
            reason = None
            if pnl_pct <= stop: reason = 1
            elif sma_v[idx] > 0 and c >= sma_v[idx]: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * US_COMMISSION / 100
                capital_usd += cost + (rev - cost - comm)
                trades_pnl.append((rev - cost - comm) * EXCHANGE_RATE)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, data in us_data.items():
                if len(positions) >= 3: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < max(sma_per + 1, 15): continue
                sma_v = sma(data["closes"][:idx+1], sma_per)
                if sma_v[idx] <= 0: continue
                dev = (data["closes"][idx] - sma_v[idx]) / sma_v[idx] * 100
                if dev > -deviation: continue
                if rsi_thresh:
                    rv = rsi(data["closes"][:idx+1], 14)
                    if rv[idx] > rsi_thresh: continue
                budget = capital_usd / 3
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital_usd -= data["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": data["closes"][idx], "ed": date})

        eq_usd = capital_usd + sum(
            us_data[p["s"]]["closes"][us_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in us_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq_usd * EXCHANGE_RATE)

    return calc_result(trades_pnl, equity, US_CAPITAL)


# ─── US 전략: 거래량 급등 ─────────────────────────────────
def strategy_us_vol_spike(us_data, vol_mult=2.0, target=8.0, max_hold=7):
    all_dates = set()
    for d in us_data.values(): all_dates.update(d["dates"])
    dates = sorted(d for d in all_dates if START <= d <= END)
    capital_usd = US_CAPITAL / EXCHANGE_RATE
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            data = us_data.get(pos["s"])
            if not data or date not in data["dates"]: continue
            idx = data["dates"].index(date)
            c = data["closes"][idx]
            pnl_pct = (c - pos["ep"]) / pos["ep"] * 100
            hold = idx - data["dates"].index(pos["ed"]) if pos["ed"] in data["dates"] else 0
            avg_vol = sum(data["volumes"][max(0,idx-20):idx]) / min(20, idx) if idx > 0 else 1
            reason = None
            if c <= pos.get("sl", 0): reason = 1
            elif pnl_pct <= -5: reason = 1
            elif pnl_pct >= target: reason = 1
            elif hold >= max_hold: reason = 1
            elif hold >= 2 and data["volumes"][idx] < avg_vol: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = c * pos["q"]
                comm = (cost + rev) * US_COMMISSION / 100
                capital_usd += cost + (rev - cost - comm)
                trades_pnl.append((rev - cost - comm) * EXCHANGE_RATE)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, data in us_data.items():
                if len(positions) >= 3: break
                if date not in data["dates"] or any(p["s"] == sym for p in positions): continue
                idx = data["dates"].index(date)
                if idx < 21: continue
                if data["closes"][idx] <= data["opens"][idx]: continue
                avg_vol = sum(data["volumes"][max(0,idx-20):idx]) / 20
                if avg_vol <= 0 or data["volumes"][idx] < avg_vol * vol_mult: continue
                budget = capital_usd / 3
                qty = int(budget / data["closes"][idx])
                if qty <= 0: continue
                capital_usd -= data["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": data["closes"][idx], "ed": date,
                                  "sl": data["lows"][idx]})

        eq_usd = capital_usd + sum(
            us_data[p["s"]]["closes"][us_data[p["s"]]["dates"].index(date)] * p["q"]
            if date in us_data[p["s"]]["dates"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq_usd * EXCHANGE_RATE)

    return calc_result(trades_pnl, equity, US_CAPITAL)


# ═══════════════════════════════════════════════════════════
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # ── 국내 데이터 로드 (1회) ────────────────────────────
    logger.info("국내 데이터 로딩...")
    symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
    kr_data = {}
    for sym in symbols:
        data = get_daily_ohlcv(sym, days=400)
        if data and len(data.get("closes", [])) >= 60:
            kr_data[sym] = data
    kr_dates = sorted(set(d for data in kr_data.values() for d in data["dates"]))
    logger.info(f"국내 {len(kr_data)}종목, {len(kr_dates)}일 로드 완료")

    # ── 미국 데이터 로드 (1회) ────────────────────────────
    logger.info("미국 데이터 로딩...")
    us_data = bulk_download(US_SYMBOLS, days=400)
    logger.info(f"미국 {len(us_data)}종목 로드 완료")

    # ── 전략 실행 ─────────────────────────────────────────
    results = []

    strategies = [
        # (이름, 함수, 파라미터 리스트)
        ("EMA(5/21)+RSI>40", strategy_ema_cross, {"ema_short": 5, "ema_long": 21, "rsi_thresh": 40}),
        ("EMA(9/21)+RSI>50", strategy_ema_cross, {"ema_short": 9, "ema_long": 21, "rsi_thresh": 50}),
        ("EMA(9/30)+RSI>40", strategy_ema_cross, {"ema_short": 9, "ema_long": 30, "rsi_thresh": 40}),
        ("EMA(13/21)+RSI>60", strategy_ema_cross, {"ema_short": 13, "ema_long": 21, "rsi_thresh": 60}),
        ("스토캐스틱(9,3,20)", strategy_stochastic, {"k_per": 9, "d_per": 3, "oversold": 20}),
        ("스토캐스틱(5,3,15)", strategy_stochastic, {"k_per": 5, "d_per": 3, "oversold": 15}),
        ("스토캐스틱(14,5,25)", strategy_stochastic, {"k_per": 14, "d_per": 5, "oversold": 25}),
        ("ORB(MA20,Vol1.5,TP5%)", strategy_orb, {"ma_period": 20, "vol_mult": 1.5, "target": 5.0}),
        ("ORB(MA50,Vol1.2,TP7%)", strategy_orb, {"ma_period": 50, "vol_mult": 1.2, "target": 7.0}),
        ("ORB(없음,Vol2.0,TP3%)", strategy_orb, {"ma_period": None, "vol_mult": 2.0, "target": 3.0}),
        ("피봇(T1%→R1,SL-2%)", strategy_pivot, {"touch_pct": 1.0, "exit_target": "R1", "stop": -2.0}),
        ("피봇(T0.5%→P,SL-1.5%)", strategy_pivot, {"touch_pct": 0.5, "exit_target": "P", "stop": -1.5}),
        ("피봇(T1.5%→R1,SL-3%)", strategy_pivot, {"touch_pct": 1.5, "exit_target": "R1", "stop": -3.0}),
        ("삼중(RSI35,Vol1.5,SL-3%)", strategy_triple, {"rsi_thresh": 35, "vol_mult": 1.5, "stop": -3.0}),
        ("삼중(RSI30,Vol1.2,SL-2%)", strategy_triple, {"rsi_thresh": 30, "vol_mult": 1.2, "stop": -2.0}),
        ("삼중(RSI40,Vol2.0,SL-4%)", strategy_triple, {"rsi_thresh": 40, "vol_mult": 2.0, "stop": -4.0}),
        ("ADX>25,SL2.0ATR,TP3.0ATR", strategy_adx, {"adx_thresh": 25, "atr_sl": 2.0, "atr_tp": 3.0}),
        ("ADX>20,SL1.5ATR,TP2.0ATR", strategy_adx, {"adx_thresh": 20, "atr_sl": 1.5, "atr_tp": 2.0}),
        ("ADX>30,SL2.5ATR,TP4.0ATR", strategy_adx, {"adx_thresh": 30, "atr_sl": 2.5, "atr_tp": 4.0}),
        ("볼린저(2.0σ,RSI30,SL-3%)", strategy_bollinger, {"bb_std": 2.0, "rsi_thresh": 30, "stop": -3.0}),
        ("볼린저(1.5σ,RSI35,SL-2%)", strategy_bollinger, {"bb_std": 1.5, "rsi_thresh": 35, "stop": -2.0}),
        ("볼린저(2.5σ,RSI25,SL-5%)", strategy_bollinger, {"bb_std": 2.5, "rsi_thresh": 25, "stop": -5.0}),
        ("급락반등(3일,-3%)", strategy_reversal, {"consec": 3, "bounce_pct": 3.0, "stop": -5.0}),
        ("급락반등(2일,-2%)", strategy_reversal, {"consec": 2, "bounce_pct": 2.0, "stop": -3.0}),
        ("급락반등(4일,-5%)", strategy_reversal, {"consec": 4, "bounce_pct": 5.0, "stop": -5.0}),
        ("변동성돌파(K0.5,MA)", strategy_volatility, {"k": 0.5, "use_ma": True}),
        ("변동성돌파(K0.3,MA)", strategy_volatility, {"k": 0.3, "use_ma": True}),
        ("변동성돌파(K0.7,noMA)", strategy_volatility, {"k": 0.7, "use_ma": False}),
        ("ATR과매도(2.0x,SL-5%)", strategy_atr_oversold, {"atr_mult": 2.0, "stop": -5.0}),
        ("ATR과매도(1.5x,SL-3%)", strategy_atr_oversold, {"atr_mult": 1.5, "stop": -3.0}),
        ("ATR과매도(2.5x,SL-5%)", strategy_atr_oversold, {"atr_mult": 2.5, "stop": -5.0}),
        # ── 신규 전략 ──
        ("RSI2(10/70,MA200)", strategy_rsi2_connors, {"rsi_period": 2, "rsi_entry": 10, "rsi_exit": 70, "ma_period": 200}),
        ("RSI2(5/80,MA200)", strategy_rsi2_connors, {"rsi_period": 2, "rsi_entry": 5, "rsi_exit": 80, "ma_period": 200}),
        ("RSI2(15/60,MA100)", strategy_rsi2_connors, {"rsi_period": 2, "rsi_entry": 15, "rsi_exit": 60, "ma_period": 100}),
        ("RSI2(10/70,MA100)", strategy_rsi2_connors, {"rsi_period": 2, "rsi_entry": 10, "rsi_exit": 70, "ma_period": 100}),
        ("듀얼모멘텀(20d,T5,R5)", strategy_dual_momentum, {"lookback": 20, "top_n": 5, "rebal_days": 5}),
        ("듀얼모멘텀(10d,T3,R3)", strategy_dual_momentum, {"lookback": 10, "top_n": 3, "rebal_days": 3}),
        ("듀얼모멘텀(5d,T3,R3)", strategy_dual_momentum, {"lookback": 5, "top_n": 3, "rebal_days": 3}),
        ("콤보ADX+EMA+RSI(25,13/21)", strategy_combo_adx_ema_rsi, {"adx_thresh": 25, "ema_short": 13, "ema_long": 21, "rsi_low": 40, "rsi_high": 70, "atr_sl": 2.0, "atr_tp": 4.0}),
        ("콤보ADX+EMA+RSI(20,9/21)", strategy_combo_adx_ema_rsi, {"adx_thresh": 20, "ema_short": 9, "ema_long": 21, "rsi_low": 35, "rsi_high": 75, "atr_sl": 1.5, "atr_tp": 3.0}),
        ("콤보ADX+EMA+RSI(30,13/34)", strategy_combo_adx_ema_rsi, {"adx_thresh": 30, "ema_short": 13, "ema_long": 34, "rsi_low": 40, "rsi_high": 70, "atr_sl": 2.5, "atr_tp": 5.0}),
        ("MACD(12/26/9)+Vol1.5x", strategy_macd_volume, {"fast": 12, "slow": 26, "signal_p": 9, "vol_mult": 1.5}),
        ("MACD(8/21/5)+Vol1.3x", strategy_macd_volume, {"fast": 8, "slow": 21, "signal_p": 5, "vol_mult": 1.3}),
        ("MACD(12/26/9)+Vol2.0x", strategy_macd_volume, {"fast": 12, "slow": 26, "signal_p": 9, "vol_mult": 2.0}),
        ("스퀴즈(BB2.0,KC1.5)", strategy_keltner_squeeze, {"bb_std": 2.0, "kc_mult": 1.5, "atr_sl": 2.0, "atr_tp": 3.5}),
        ("스퀴즈(BB2.0,KC1.0)", strategy_keltner_squeeze, {"bb_std": 2.0, "kc_mult": 1.0, "atr_sl": 2.0, "atr_tp": 4.0}),
        ("스퀴즈(BB1.5,KC1.5)", strategy_keltner_squeeze, {"bb_std": 1.5, "kc_mult": 1.5, "atr_sl": 1.5, "atr_tp": 3.0}),
        ("도치안(20/10,ADX20)", strategy_donchian, {"entry_period": 20, "exit_period": 10}),
        ("도치안(10/5,ADX20)", strategy_donchian, {"entry_period": 10, "exit_period": 5}),
        ("도치안(30/15,ADX20)", strategy_donchian, {"entry_period": 30, "exit_period": 15}),
        ("상대강도(10d,T3,R5)", strategy_relative_strength, {"lookback": 10, "top_n": 3, "rebal_days": 5}),
        ("상대강도(5d,T3,R3)", strategy_relative_strength, {"lookback": 5, "top_n": 3, "rebal_days": 3}),
        ("상대강도(5d,T5,R3)", strategy_relative_strength, {"lookback": 5, "top_n": 5, "rebal_days": 3}),
        ("상대강도(3d,T3,R2)", strategy_relative_strength, {"lookback": 3, "top_n": 3, "rebal_days": 2}),
    ]

    # 국내 전략
    for name, func, params in strategies:
        t0 = time.time()
        r = func(kr_data, kr_dates, **params)
        r["전략"] = f"[KR] {name}"
        results.append(r)
        elapsed = time.time() - t0
        logger.info(f"  {r['전략']:40s} → 수익률:{r['수익률']:+6.1f}% 거래:{r['거래']:4d} PF:{r['PF']:5.2f} ({elapsed:.1f}s)")

    # 미국 전략
    us_strategies = [
        ("MR(SMA20,Dev3%,RSI30)", strategy_us_mean_rev, {"sma_per": 20, "deviation": 3.0, "rsi_thresh": 30}),
        ("MR(SMA20,Dev5%,RSI30)", strategy_us_mean_rev, {"sma_per": 20, "deviation": 5.0, "rsi_thresh": 30}),
        ("MR(SMA10,Dev5%,RSI25)", strategy_us_mean_rev, {"sma_per": 10, "deviation": 5.0, "rsi_thresh": 25}),
        ("MR(SMA30,Dev3%,RSI30)", strategy_us_mean_rev, {"sma_per": 30, "deviation": 3.0, "rsi_thresh": 30}),
        ("MR(SMA20,없음)", strategy_us_mean_rev, {"sma_per": 20, "deviation": 3.0, "rsi_thresh": None}),
        ("VolSpike(2.0x,TP8%,H7)", strategy_us_vol_spike, {"vol_mult": 2.0, "target": 8.0, "max_hold": 7}),
        ("VolSpike(3.0x,TP8%,H7)", strategy_us_vol_spike, {"vol_mult": 3.0, "target": 8.0, "max_hold": 7}),
        ("VolSpike(4.0x,TP12%,H7)", strategy_us_vol_spike, {"vol_mult": 4.0, "target": 12.0, "max_hold": 7}),
        ("VolSpike(2.5x,TP5%,H5)", strategy_us_vol_spike, {"vol_mult": 2.5, "target": 5.0, "max_hold": 5}),
    ]

    for name, func, params in us_strategies:
        t0 = time.time()
        r = func(us_data, **params)
        r["전략"] = f"[US] {name}"
        results.append(r)
        elapsed = time.time() - t0
        logger.info(f"  {r['전략']:40s} → 수익률:{r['수익률']:+6.1f}% 거래:{r['거래']:4d} PF:{r['PF']:5.2f} ({elapsed:.1f}s)")

    # ── 결과 정렬 & 출력 ──────────────────────────────────
    results.sort(key=lambda x: x["수익률"], reverse=True)

    print(f"\n{'='*95}")
    print(f"  전체 전략 백테스트 결과 ({START} ~ {END})  |  국내 {KR_CAPITAL:,}원 / 미국 {US_CAPITAL:,}원")
    print(f"{'='*95}")
    print(f"{'#':>3} {'전략':>42} {'거래':>5} {'승률%':>6} {'수익률%':>8} {'총손익':>12} {'MDD%':>7} {'PF':>6}")
    print(f"{'-'*95}")
    for i, r in enumerate(results):
        print(f"{i+1:>3} {r['전략']:>42} {r['거래']:>5} {r['승률']:>6.1f} {r['수익률']:>+8.1f} "
              f"{r['총손익']:>+12,} {r['MDD']:>7.1f} {r['PF']:>6.2f}")

    print(f"\n{'='*95}")
    if results and results[0]["수익률"] > 0:
        b = results[0]
        print(f"  🏆 1위: {b['전략']}")
        print(f"     수익률: {b['수익률']:+.1f}% | PF: {b['PF']:.2f} | MDD: {b['MDD']:.1f}% | 승률: {b['승률']:.1f}% | 거래: {b['거래']}회")
    print(f"{'='*95}")


if __name__ == "__main__":
    main()
