"""신규 전략 백테스트 — 지표 사전계산 + O(1) 인덱싱 최적화"""

import logging
import sys
import time
from collector.market_data import get_kospi200_symbols, get_kosdaq150_symbols, get_daily_ohlcv
from strategy.indicators import sma, ema, rsi, adx, atr, bollinger_bands

logger = logging.getLogger(__name__)

START = "20250201"
END = "20260331"
KR_CAPITAL = 1_000_000
KR_COMMISSION = 0.31


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


def precompute(all_data):
    """종목별 지표를 미리 계산하고 date→idx 딕셔너리 생성"""
    cache = {}
    for sym, data in all_data.items():
        closes = data["closes"]
        highs = data["highs"]
        lows = data["lows"]
        n = len(closes)
        if n < 60:
            continue
        date_idx = {d: i for i, d in enumerate(data["dates"])}
        cache[sym] = {
            "closes": closes, "highs": highs, "lows": lows,
            "volumes": data["volumes"], "dates": data["dates"],
            "di": date_idx,
            "ema5": ema(closes, 5), "ema9": ema(closes, 9),
            "ema13": ema(closes, 13), "ema21": ema(closes, 21),
            "ema34": ema(closes, 34),
            "sma100": sma(closes, 100), "sma200": sma(closes, 200),
            "rsi2": rsi(closes, 2), "rsi14": rsi(closes, 14),
            "adx14": adx(highs, lows, closes, 14),
            "atr14": atr(highs, lows, closes, 14),
            "bb_u": bollinger_bands(closes, 20, 2.0)[0],
            "bb_m": bollinger_bands(closes, 20, 2.0)[1],
            "bb_l": bollinger_bands(closes, 20, 2.0)[2],
            "bb15_u": bollinger_bands(closes, 20, 1.5)[0],
            "bb15_m": bollinger_bands(closes, 20, 1.5)[1],
            "bb15_l": bollinger_bands(closes, 20, 1.5)[2],
            "ema12": ema(closes, 12), "ema26": ema(closes, 26),
            "ema8": ema(closes, 8),
        }
        # MACD
        e12 = cache[sym]["ema12"]
        e26 = cache[sym]["ema26"]
        macd_line = [e12[i] - e26[i] for i in range(n)]
        cache[sym]["macd"] = macd_line
        cache[sym]["macd_sig9"] = ema(macd_line, 9)
        e8 = cache[sym]["ema8"]
        e21 = cache[sym]["ema21"]
        macd2 = [e8[i] - e21[i] for i in range(n)]
        cache[sym]["macd2"] = macd2
        cache[sym]["macd2_sig5"] = ema(macd2, 5)
    return cache


# ═══════════════════════════════════════════════════════════
# 전략 0: RSI(2) + Holy Grail 최적화 (config 반영)
# ADX>20, RSI(2)<5 진입, RSI(2)>80 익절, 손절-4%, 추적-2.5%, 보유10일
# ═══════════════════════════════════════════════════════════
def strategy_rsi2_holy_grail(cache, all_dates, rsi_entry=5, rsi_exit=80,
                              adx_min=20, stop=-4.0, trailing=-2.5, max_hold=10):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            # 고점 갱신
            pos["hp"] = max(pos.get("hp", pos["ep"]), price)
            trail_pct = (price - pos["hp"]) / pos["hp"] * 100

            reason = None
            if pnl_pct <= stop:
                reason = 1  # 고정 손절
            elif trail_pct <= trailing and pos["hp"] > pos["ep"]:
                reason = 1  # 추적 손절
            elif c["rsi2"][idx] >= rsi_exit:
                reason = 1  # RSI 익절
            elif hold >= max_hold:
                reason = 1  # 최대 보유일

            if reason:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 2:  # 최대 2종목
            candidates = []
            for sym, c in cache.items():
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 200: continue
                closes = c["closes"]
                if closes[idx] < 5000: continue
                # 200MA 위
                if c["sma200"][idx] <= 0 or closes[idx] <= c["sma200"][idx]: continue
                # ADX > adx_min
                if c["adx14"][idx] < adx_min: continue
                # RSI(2) < rsi_entry
                if c["rsi2"][idx] < rsi_entry:
                    candidates.append((sym, c["rsi2"][idx]))
            candidates.sort(key=lambda x: x[1])  # RSI 가장 낮은 순
            for sym, _ in candidates[:2 - len(positions)]:
                c = cache[sym]
                idx = c["di"][date]
                budget = capital // 2
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx, "hp": c["closes"][idx]})

        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 0b: RSI2+HG N종목 버전
# ═══════════════════════════════════════════════════════════
def strategy_rsi2_holy_grail_n(cache, all_dates, rsi_entry=5, rsi_exit=80,
                                adx_min=20, stop=-4.0, trailing=-2.5, max_hold=10, max_pos=3):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []
    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            pos["hp"] = max(pos.get("hp", pos["ep"]), price)
            trail_pct = (price - pos["hp"]) / pos["hp"] * 100
            reason = None
            if pnl_pct <= stop: reason = 1
            elif trail_pct <= trailing and pos["hp"] > pos["ep"]: reason = 1
            elif c["rsi2"][idx] >= rsi_exit: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]
        if len(positions) < max_pos:
            candidates = []
            for sym, c in cache.items():
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 200 or c["closes"][idx] < 5000: continue
                if c["sma200"][idx] <= 0 or c["closes"][idx] <= c["sma200"][idx]: continue
                if c["adx14"][idx] < adx_min: continue
                if c["rsi2"][idx] < rsi_entry:
                    candidates.append((sym, c["rsi2"][idx]))
            candidates.sort(key=lambda x: x[1])
            for sym, _ in candidates[:max_pos - len(positions)]:
                c = cache[sym]
                idx = c["di"][date]
                budget = capital // max_pos
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx, "hp": c["closes"][idx]})
        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)
    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 0c: EMA 크로스 + RSI + 추적손절 옵션
# ═══════════════════════════════════════════════════════════
def strategy_ema_cross(cache, all_dates, es="ema13", el="ema21", rsi_thresh=60,
                       stop=-5.0, target=5.0, trailing=None, max_hold=10):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []
    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            pos["hp"] = max(pos.get("hp", pos["ep"]), price)
            trail_pct = (price - pos["hp"]) / pos["hp"] * 100
            es_v = c[es]; el_v = c[el]
            cross_down = idx > 0 and es_v[idx] < el_v[idx] and es_v[idx-1] >= el_v[idx-1]
            reason = None
            if pnl_pct <= stop: reason = 1
            elif pnl_pct >= target: reason = 1
            elif trailing and trail_pct <= trailing and pos["hp"] > pos["ep"]: reason = 1
            elif hold >= max_hold: reason = 1
            elif cross_down: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]
        if len(positions) < 3:
            candidates = []
            for sym, c in cache.items():
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 60 or c["closes"][idx] < 5000: continue
                es_v = c[es]; el_v = c[el]
                if idx < 1 or not (es_v[idx] > el_v[idx] and es_v[idx-1] <= el_v[idx-1]): continue
                if c["rsi14"][idx] < rsi_thresh: continue
                candidates.append((sym, c["rsi14"][idx], idx))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for sym, _, idx in candidates[:3 - len(positions)]:
                c = cache[sym]
                budget = capital // 3
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx, "hp": c["closes"][idx]})
        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)
    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 0d: ADX + ATR 기반 추세추종 + 추적손절 옵션
# ═══════════════════════════════════════════════════════════
def strategy_adx_atr(cache, all_dates, adx_thresh=30, atr_sl=2.5, atr_tp=4.0,
                     trailing=None, max_hold=15):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []
    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            a = c["atr14"][idx]
            sl_pct = -(atr_sl * a / pos["ep"] * 100) if a > 0 else -5.0
            tp_pct = (atr_tp * a / pos["ep"] * 100) if a > 0 else 10.0
            pos["hp"] = max(pos.get("hp", pos["ep"]), price)
            trail_pct = (price - pos["hp"]) / pos["hp"] * 100
            reason = None
            if pnl_pct <= sl_pct: reason = 1
            elif pnl_pct >= tp_pct: reason = 1
            elif trailing and trail_pct <= trailing and pos["hp"] > pos["ep"]: reason = 1
            elif hold >= max_hold: reason = 1
            if reason:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]
        if len(positions) < 3:
            for sym, c in cache.items():
                if len(positions) >= 3: break
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 60 or c["closes"][idx] < 5000: continue
                if c["adx14"][idx] < adx_thresh: continue
                # +DI > -DI 체크 (상승 추세만) — ema 짧은 > 긴
                if c["ema13"][idx] <= c["ema21"][idx]: continue
                budget = capital // 3
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx, "hp": c["closes"][idx]})
        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)
    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 1: RSI(2) Connors — 극단적 과매도 평균회귀
# ═══════════════════════════════════════════════════════════
def strategy_rsi2(cache, all_dates, rsi_entry=10, rsi_exit=70, ma_key="sma200", stop=-5.0):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        # 매도 체크
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            if pnl_pct <= stop or c["rsi2"][idx] >= rsi_exit:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        # 매수
        if len(positions) < 5:
            candidates = []
            for sym, c in cache.items():
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 200: continue
                closes = c["closes"]
                if closes[idx] < 5000: continue
                if c[ma_key][idx] <= 0 or closes[idx] < c[ma_key][idx]: continue
                if c["rsi2"][idx] < rsi_entry:
                    candidates.append((sym, c["rsi2"][idx]))
            candidates.sort(key=lambda x: x[1])
            for sym, _ in candidates[:5 - len(positions)]:
                c = cache[sym]
                idx = c["di"][date]
                budget = capital // 5
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ed": date})

        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 2: 듀얼 모멘텀 (절대+상대)
# ═══════════════════════════════════════════════════════════
def strategy_dual_momentum(cache, all_dates, lookback=20, top_n=5, rebal_days=5, stop=-7.0):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = {}
    trades_pnl = []
    equity = []
    day_count = 0

    for date in dates:
        day_count += 1
        for sym in list(positions.keys()):
            c = cache.get(sym)
            if not c or date not in c["di"]: continue
            idx = c["di"][date]
            pnl_pct = (c["closes"][idx] - positions[sym]["ep"]) / positions[sym]["ep"] * 100
            if pnl_pct <= stop:
                cost = positions[sym]["ep"] * positions[sym]["q"]
                rev = c["closes"][idx] * positions[sym]["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

        if day_count % rebal_days == 1:
            scores = []
            for sym, c in cache.items():
                if date not in c["di"]: continue
                idx = c["di"][date]
                if idx < lookback: continue
                ret = (c["closes"][idx] - c["closes"][idx - lookback]) / c["closes"][idx - lookback] * 100
                if ret <= 0: continue
                if c["closes"][idx] < 5000: continue
                scores.append((sym, ret))
            scores.sort(key=lambda x: x[1], reverse=True)
            target = set(s for s, _ in scores[:top_n])

            for sym in list(positions.keys()):
                if sym in target: continue
                c = cache.get(sym)
                if not c or date not in c["di"]: continue
                idx = c["di"][date]
                cost = positions[sym]["ep"] * positions[sym]["q"]
                rev = c["closes"][idx] * positions[sym]["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

            for sym in target:
                if sym in positions: continue
                c = cache.get(sym)
                if not c or date not in c["di"]: continue
                idx = c["di"][date]
                budget = capital // max(1, top_n - len(positions))
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions[sym] = {"q": qty, "ep": c["closes"][idx]}

        eq = capital + sum(
            cache[s]["closes"][cache[s]["di"][date]] * p["q"]
            if date in cache[s]["di"] else p["ep"] * p["q"]
            for s, p in positions.items())
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 3: ADX + EMA + RSI 콤보
# ═══════════════════════════════════════════════════════════
def strategy_combo(cache, all_dates, adx_thresh=25, es_key="ema13", el_key="ema21",
                   rsi_low=40, rsi_high=70, atr_sl=2.0, atr_tp=4.0, max_hold=15):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            a = c["atr14"][idx]
            sl_pct = -(atr_sl * a / pos["ep"] * 100) if a > 0 else -3.0
            tp_pct = (atr_tp * a / pos["ep"] * 100) if a > 0 else 6.0
            if pnl_pct <= sl_pct or pnl_pct >= tp_pct or hold >= max_hold:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            candidates = []
            for sym, c in cache.items():
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 60 or c["closes"][idx] < 5000: continue
                if c["adx14"][idx] < adx_thresh: continue
                es = c[es_key]; el = c[el_key]
                if idx < 1 or not (es[idx] > el[idx] and es[idx-1] <= el[idx-1]): continue
                r14 = c["rsi14"][idx]
                if r14 < rsi_low or r14 > rsi_high: continue
                candidates.append((sym, c["adx14"][idx], idx))
            candidates.sort(key=lambda x: x[1], reverse=True)
            for sym, _, idx in candidates[:3 - len(positions)]:
                c = cache[sym]
                budget = capital // 3
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx})

        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 4: MACD 골든크로스 + 거래량
# ═══════════════════════════════════════════════════════════
def strategy_macd_vol(cache, all_dates, macd_key="macd", sig_key="macd_sig9",
                      vol_mult=1.5, stop=-4.0, target=8.0, max_hold=12):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            m = c[macd_key]; s = c[sig_key]
            dead = idx > 0 and m[idx] < s[idx] and m[idx-1] >= s[idx-1]
            if pnl_pct <= stop or pnl_pct >= target or hold >= max_hold or (dead and pnl_pct > 0):
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, c in cache.items():
                if len(positions) >= 3: break
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 40 or c["closes"][idx] < 5000: continue
                m = c[macd_key]; s = c[sig_key]
                if not (m[idx] > s[idx] and m[idx-1] <= s[idx-1]): continue
                avg_vol = sum(c["volumes"][max(0,idx-20):idx]) / min(20, idx) if idx > 0 else 1
                if avg_vol <= 0 or c["volumes"][idx] < avg_vol * vol_mult: continue
                budget = capital // 3
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx})

        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 5: 켈트너 스퀴즈 브레이크아웃
# ═══════════════════════════════════════════════════════════
def strategy_squeeze(cache, all_dates, bb_u_key="bb_u", bb_l_key="bb_l", bb_m_key="bb_m",
                     kc_mult=1.5, atr_sl=2.0, atr_tp=3.5, max_hold=10):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            a = c["atr14"][idx]
            sl_pct = -(atr_sl * a / pos["ep"] * 100) if a > 0 else -3.0
            tp_pct = (atr_tp * a / pos["ep"] * 100) if a > 0 else 7.0
            if pnl_pct <= sl_pct or pnl_pct >= tp_pct or hold >= max_hold:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, c in cache.items():
                if len(positions) >= 3: break
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < 25 or c["closes"][idx] < 5000: continue
                bu = c[bb_u_key]; bl = c[bb_l_key]; bm = c[bb_m_key]
                a = c["atr14"][idx]
                if a <= 0 or bm[idx] <= 0: continue
                kc_u = bm[idx] + kc_mult * a
                kc_l = bm[idx] - kc_mult * a
                prev_sq = idx > 0 and bu[idx-1] < kc_u and bl[idx-1] > kc_l
                curr_brk = bu[idx] >= kc_u
                if not (prev_sq and curr_brk): continue
                if c["closes"][idx] <= bm[idx]: continue
                budget = capital // 3
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx})

        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 6: 도치안 채널 돌파 (터틀)
# ═══════════════════════════════════════════════════════════
def strategy_donchian(cache, all_dates, entry_period=20, exit_period=10, max_hold=20):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = []
    trades_pnl = []
    equity = []

    for date in dates:
        for pos in list(positions):
            c = cache[pos["s"]]
            if date not in c["di"]: continue
            idx = c["di"][date]
            price = c["closes"][idx]
            pnl_pct = (price - pos["ep"]) / pos["ep"] * 100
            hold = idx - pos["ei"]
            exit_low = min(c["lows"][max(0,idx-exit_period):idx]) if idx > 0 else c["lows"][idx]
            if price <= exit_low or pnl_pct <= -8.0 or hold >= max_hold:
                cost = pos["ep"] * pos["q"]
                rev = price * pos["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                positions = [p for p in positions if p["s"] != pos["s"]]

        if len(positions) < 3:
            for sym, c in cache.items():
                if len(positions) >= 3: break
                if date not in c["di"] or any(p["s"] == sym for p in positions): continue
                idx = c["di"][date]
                if idx < entry_period + 5 or c["closes"][idx] < 5000: continue
                entry_high = max(c["highs"][idx-entry_period:idx])
                if c["closes"][idx] <= entry_high: continue
                if c["adx14"][idx] < 20: continue
                budget = capital // 3
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions.append({"s": sym, "q": qty, "ep": c["closes"][idx], "ei": idx})

        eq = capital + sum(
            cache[p["s"]]["closes"][cache[p["s"]]["di"][date]] * p["q"]
            if date in cache[p["s"]]["di"] else p["ep"] * p["q"]
            for p in positions)
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
# 전략 7: 상대강도 로테이션
# ═══════════════════════════════════════════════════════════
def strategy_rel_strength(cache, all_dates, lookback=10, top_n=3, rebal_days=5, stop=-5.0):
    dates = [d for d in all_dates if START <= d <= END]
    capital = KR_CAPITAL
    positions = {}
    trades_pnl = []
    equity = []
    day_count = 0

    for date in dates:
        day_count += 1
        for sym in list(positions.keys()):
            c = cache.get(sym)
            if not c or date not in c["di"]: continue
            idx = c["di"][date]
            pnl_pct = (c["closes"][idx] - positions[sym]["ep"]) / positions[sym]["ep"] * 100
            if pnl_pct <= stop:
                cost = positions[sym]["ep"] * positions[sym]["q"]
                rev = c["closes"][idx] * positions[sym]["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

        if day_count % rebal_days == 1:
            scores = []
            for sym, c in cache.items():
                if date not in c["di"]: continue
                idx = c["di"][date]
                if idx < lookback or c["closes"][idx] < 5000: continue
                ret = (c["closes"][idx] - c["closes"][idx - lookback]) / c["closes"][idx - lookback] * 100
                if ret <= 0: continue
                scores.append((sym, ret))
            scores.sort(key=lambda x: x[1], reverse=True)
            target = set(s for s, _ in scores[:top_n])

            for sym in list(positions.keys()):
                if sym in target: continue
                c = cache.get(sym)
                if not c or date not in c["di"]: continue
                idx = c["di"][date]
                cost = positions[sym]["ep"] * positions[sym]["q"]
                rev = c["closes"][idx] * positions[sym]["q"]
                comm = (cost + rev) * KR_COMMISSION / 100
                capital += cost + (rev - cost - comm)
                trades_pnl.append(rev - cost - comm)
                del positions[sym]

            for sym in target:
                if sym in positions: continue
                c = cache.get(sym)
                if not c or date not in c["di"]: continue
                idx = c["di"][date]
                budget = capital // max(1, top_n - len(positions))
                qty = int(budget / c["closes"][idx])
                if qty <= 0: continue
                capital -= c["closes"][idx] * qty
                positions[sym] = {"q": qty, "ep": c["closes"][idx]}

        eq = capital + sum(
            cache[s]["closes"][cache[s]["di"][date]] * p["q"]
            if date in cache[s]["di"] else p["ep"] * p["q"]
            for s, p in positions.items())
        equity.append(eq)

    return calc_result(trades_pnl, equity, KR_CAPITAL)


# ═══════════════════════════════════════════════════════════
def get_strategies():
    """전 전략 파라미터 튜닝 비교"""
    return [
        # ══ RSI2+HG 파라미터 스윕 ══
        ("RSI2+HG(현재config)", strategy_rsi2_holy_grail, {"rsi_entry": 5, "rsi_exit": 80, "adx_min": 20, "stop": -4.0, "trailing": -2.5, "max_hold": 10}),
        ("RSI2+HG(exit90)", strategy_rsi2_holy_grail, {"rsi_entry": 5, "rsi_exit": 90, "adx_min": 20, "stop": -4.0, "trailing": -2.5, "max_hold": 10}),
        ("RSI2+HG(adx15)", strategy_rsi2_holy_grail, {"rsi_entry": 5, "rsi_exit": 80, "adx_min": 15, "stop": -4.0, "trailing": -2.5, "max_hold": 10}),
        ("RSI2+HG(entry10)", strategy_rsi2_holy_grail, {"rsi_entry": 10, "rsi_exit": 80, "adx_min": 20, "stop": -4.0, "trailing": -2.5, "max_hold": 10}),
        ("RSI2+HG(sl-5,hold15)", strategy_rsi2_holy_grail, {"rsi_entry": 5, "rsi_exit": 80, "adx_min": 20, "stop": -5.0, "trailing": -3.0, "max_hold": 15}),
        ("RSI2+HG(sl-3,trail-2)", strategy_rsi2_holy_grail, {"rsi_entry": 5, "rsi_exit": 80, "adx_min": 20, "stop": -3.0, "trailing": -2.0, "max_hold": 10}),
        ("RSI2+HG(3종목)", strategy_rsi2_holy_grail_n, {"rsi_entry": 5, "rsi_exit": 80, "adx_min": 20, "stop": -4.0, "trailing": -2.5, "max_hold": 10, "max_pos": 3}),
        ("RSI2+HG(entry10,exit90)", strategy_rsi2_holy_grail, {"rsi_entry": 10, "rsi_exit": 90, "adx_min": 20, "stop": -4.0, "trailing": -2.5, "max_hold": 10}),
        # ══ EMA 크로스 파라미터 스윕 (원래 1위 +63.2%) ══
        ("EMA(13/21)+RSI>60", strategy_ema_cross, {"es": "ema13", "el": "ema21", "rsi_thresh": 60, "stop": -5.0, "target": 5.0, "trailing": None}),
        ("EMA(13/21)+RSI>60+trail", strategy_ema_cross, {"es": "ema13", "el": "ema21", "rsi_thresh": 60, "stop": -5.0, "target": 8.0, "trailing": -2.5}),
        ("EMA(13/21)+RSI>50+trail", strategy_ema_cross, {"es": "ema13", "el": "ema21", "rsi_thresh": 50, "stop": -4.0, "target": 8.0, "trailing": -2.5}),
        ("EMA(9/21)+RSI>50+trail", strategy_ema_cross, {"es": "ema9", "el": "ema21", "rsi_thresh": 50, "stop": -4.0, "target": 8.0, "trailing": -2.5}),
        ("EMA(13/34)+RSI>50+trail", strategy_ema_cross, {"es": "ema13", "el": "ema34", "rsi_thresh": 50, "stop": -4.0, "target": 10.0, "trailing": -3.0}),
        # ══ ADX+ATR 파라미터 스윕 (원래 2위 +56.4%) ══
        ("ADX(30)+2.5/4.0ATR", strategy_adx_atr, {"adx_thresh": 30, "atr_sl": 2.5, "atr_tp": 4.0, "trailing": None}),
        ("ADX(25)+2.0/4.0ATR+trail", strategy_adx_atr, {"adx_thresh": 25, "atr_sl": 2.0, "atr_tp": 4.0, "trailing": -2.5}),
        ("ADX(20)+2.0/5.0ATR+trail", strategy_adx_atr, {"adx_thresh": 20, "atr_sl": 2.0, "atr_tp": 5.0, "trailing": -3.0}),
        ("ADX(30)+2.0/5.0ATR+trail", strategy_adx_atr, {"adx_thresh": 30, "atr_sl": 2.0, "atr_tp": 5.0, "trailing": -2.5}),
        ("ADX(25)+1.5/3.0ATR", strategy_adx_atr, {"adx_thresh": 25, "atr_sl": 1.5, "atr_tp": 3.0, "trailing": None}),
        # ══ 기준선: 상대강도 ══
        ("상대강도(10d,T3,R5)", strategy_rel_strength, {"lookback": 10, "top_n": 3, "rebal_days": 5}),
    ]


def main():
    global START, END
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # ── 데이터 로드 (최대한 길게) ──
    logger.info("국내 데이터 로딩 (700일)...")
    symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
    kr_data = {}
    for sym in symbols:
        data = get_daily_ohlcv(sym, days=700)
        if data and len(data.get("closes", [])) >= 60:
            kr_data[sym] = data
    kr_dates = sorted(set(d for data in kr_data.values() for d in data["dates"]))
    logger.info(f"국내 {len(kr_data)}종목, 전체 {len(kr_dates)}일 로드 완료")
    logger.info(f"데이터 범위: {kr_dates[0]} ~ {kr_dates[-1]}")

    # ── 지표 사전계산 ──
    logger.info("지표 사전계산...")
    t0 = time.time()
    cache = precompute(kr_data)
    logger.info(f"사전계산 완료: {len(cache)}종목 ({time.time()-t0:.1f}s)")

    strategies = get_strategies()

    # ── 워크포워드: 3개월 단위 구간으로 슬라이딩 ──
    windows = []
    # 데이터에서 가능한 3개월 구간들 자동 생성
    from datetime import datetime, timedelta
    first = datetime.strptime(kr_dates[0], "%Y%m%d")
    last = datetime.strptime(kr_dates[-1], "%Y%m%d")
    # 지표 안정화를 위해 첫 200일은 건너뜀
    warmup_date = first + timedelta(days=280)
    win_start = warmup_date
    while win_start + timedelta(days=90) <= last:
        win_end = win_start + timedelta(days=90)
        windows.append((win_start.strftime("%Y%m%d"), win_end.strftime("%Y%m%d")))
        win_start += timedelta(days=30)  # 30일씩 슬라이딩

    logger.info(f"워크포워드 구간: {len(windows)}개 (3개월 단위, 30일 슬라이딩)")
    for i, (s, e) in enumerate(windows):
        logger.info(f"  구간{i+1}: {s} ~ {e}")

    # ── 구간별 실행 ──
    # all_results[전략이름] = [구간1결과, 구간2결과, ...]
    all_results = {name: [] for name, _, _ in strategies}

    for wi, (ws, we) in enumerate(windows):
        START = ws
        END = we
        for name, func, params in strategies:
            r = func(cache, kr_dates, **params)
            all_results[name].append(r)

    # ── 구간별 상세 결과 출력 ──
    print(f"\n{'='*120}")
    print(f"  워크포워드 분석 — {len(windows)}개 구간 × {len(strategies)}개 전략")
    print(f"  각 구간 = 3개월, 30일씩 슬라이딩  |  자본금 {KR_CAPITAL:,}원")
    print(f"{'='*120}")

    # 구간별 수익률 테이블
    header = f"{'전략':>28s}"
    for i, (ws, we) in enumerate(windows):
        header += f" {ws[4:6]}/{ws[6:]}-{we[4:6]}/{we[6:]}"
    header += "  |   평균  중간값  최소   최대  양수%  평균PF 평균MDD"
    print(header)
    print("-" * len(header))

    summary = []
    for name, _, _ in strategies:
        results = all_results[name]
        rets = [r["수익률"] for r in results]
        pfs = [r["PF"] for r in results]
        mdds = [r["MDD"] for r in results]
        avg_ret = sum(rets) / len(rets)
        sorted_rets = sorted(rets)
        mid = len(sorted_rets) // 2
        median_ret = sorted_rets[mid] if len(sorted_rets) % 2 else (sorted_rets[mid-1] + sorted_rets[mid]) / 2
        min_ret = min(rets)
        max_ret = max(rets)
        pos_pct = sum(1 for r in rets if r > 0) / len(rets) * 100
        avg_pf = sum(pfs) / len(pfs)
        avg_mdd = sum(mdds) / len(mdds)

        row = f"{name:>28s}"
        for r in results:
            ret = r["수익률"]
            if ret >= 20:
                row += f"  \033[32m{ret:>+6.1f}\033[0m"
            elif ret <= -10:
                row += f"  \033[31m{ret:>+6.1f}\033[0m"
            else:
                row += f"  {ret:>+6.1f}"
        row += f"  | {avg_ret:>+6.1f} {median_ret:>+6.1f} {min_ret:>+6.1f} {max_ret:>+6.1f} {pos_pct:>5.0f}% {avg_pf:>5.2f}  {avg_mdd:>5.1f}"
        print(row)

        summary.append({
            "전략": name, "평균수익률": round(avg_ret, 1), "중간값": round(median_ret, 1),
            "최소": round(min_ret, 1), "최대": round(max_ret, 1),
            "양수구간%": round(pos_pct, 0), "평균PF": round(avg_pf, 2), "평균MDD": round(avg_mdd, 1),
        })

    # ── 최종 순위 (평균 수익률 기준) ──
    summary.sort(key=lambda x: x["평균수익률"], reverse=True)
    print(f"\n{'='*100}")
    print(f"  최종 순위 — 평균 수익률 기준 (전 구간 평균)")
    print(f"{'='*100}")
    print(f"{'#':>3} {'전략':>28} {'평균%':>7} {'중간값%':>7} {'최소%':>7} {'최대%':>7} {'양수%':>6} {'평균PF':>7} {'평균MDD':>8}")
    print(f"{'-'*100}")
    for i, s in enumerate(summary):
        stable = "  ✓" if s["양수구간%"] >= 70 and s["평균PF"] >= 1.2 else ""
        print(f"{i+1:>3} {s['전략']:>28} {s['평균수익률']:>+7.1f} {s['중간값']:>+7.1f} {s['최소']:>+7.1f} "
              f"{s['최대']:>+7.1f} {s['양수구간%']:>5.0f}% {s['평균PF']:>6.2f} {s['평균MDD']:>7.1f}{stable}")
    print(f"{'='*100}")
    print(f"  ✓ = 양수구간 70%이상 & 평균PF 1.2이상 → 실전 후보")

    # ── 전체 기간 실행 (거래횟수, 총손익 확인) ──
    START = "20250201"
    END = "20260331"
    print(f"\n{'='*110}")
    print(f"  전체 기간 실적 ({START}~{END}, 14개월)  |  자본금 {KR_CAPITAL:,}원")
    print(f"{'='*110}")
    print(f"{'#':>3} {'전략':>30} {'거래':>5} {'승률%':>6} {'수익률%':>8} {'총손익':>12} {'MDD%':>7} {'PF':>6}  {'월평균손익':>10}")
    print(f"{'-'*110}")
    full_results = []
    for name, func, params in strategies:
        r = func(cache, kr_dates, **params)
        r["전략"] = name
        r["월평균"] = int(r["총손익"] / 14)
        full_results.append(r)
    full_results.sort(key=lambda x: x["수익률"], reverse=True)
    for i, r in enumerate(full_results):
        print(f"{i+1:>3} {r['전략']:>30} {r['거래']:>5} {r['승률']:>6.1f} {r['수익률']:>+8.1f} "
              f"{r['총손익']:>+12,} {r['MDD']:>7.1f} {r['PF']:>6.2f}  {r['월평균']:>+10,}원/월")
    print(f"{'='*110}")
    print()


if __name__ == "__main__":
    main()
