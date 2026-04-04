"""스캘핑 전략 백테스트 — 일봉 OHLC 기반 장중 시뮬레이션

일봉 O/H/L/C + ATR로 장중 가격 경로를 생성하여 스캘핑 시그널 테스트.
실제 분봉 대비 정밀도는 떨어지지만 필터 효과 비교에는 충분.

테스트 항목:
  A) 기존 설정 (baseline)
  B) 시간 필터 (13시 이후 차단)
  C) 가격 필터 (1만원 미만 차단)
  D) 손절 완화 (ATR 1→1.5배)
  E) 전체 필터 적용 (B+C+D)
"""

import logging, random, sys
from collector.market_data import get_kospi200_symbols, get_kosdaq150_symbols, get_daily_ohlcv
from strategy.indicators import rsi, atr, adx, sma

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)
P = lambda *a, **k: print(*a, **k, flush=True)

COMM_PCT = 0.015  # 편도 수수료 0.015%
random.seed(42)

# === 데이터 로드 ===
logger.info("데이터 로딩...")
symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
ALL_DATA = {}
for sym in symbols:
    data = get_daily_ohlcv(sym, days=600)
    if data and len(data["closes"]) >= 60:
        data["_idx"] = {d: i for i, d in enumerate(data["dates"])}
        ALL_DATA[sym] = data
ALL_DATES = sorted(set(d for data in ALL_DATA.values() for d in data["dates"]))
logger.info(f"종목: {len(ALL_DATA)}, 전체일: {len(ALL_DATES)}")


def _gen_intraday_path(o, h, l, c, steps=78):
    """일봉 OHLC로 장중 5분봉 가격 경로 생성 (78봉 = 6.5시간)

    O→(랜덤워크 with H/L 터치)→C 경로를 만듦.
    각 봉에 시각 라벨(9:00~15:30)을 부여.
    """
    if h == l:
        prices = [o] * steps
        times = [900 + (i * 5) // 60 * 100 + (i * 5) % 60 for i in range(steps)]
        return list(zip(times, prices))

    path = [o]
    # 고점/저점 터치 시점을 랜덤 배치
    high_step = random.randint(5, steps - 10)
    low_step = random.randint(5, steps - 10)
    if abs(high_step - low_step) < 5:
        low_step = min(steps - 5, high_step + 10)

    for i in range(1, steps):
        if i == high_step:
            path.append(h)
        elif i == low_step:
            path.append(l)
        elif i == steps - 1:
            path.append(c)
        else:
            # 이전 가격에서 랜덤 워크 (H/L 범위 내)
            prev = path[-1]
            noise = (h - l) * random.uniform(-0.15, 0.15)
            price = max(l, min(h, prev + noise))
            path.append(round(price))

    times = []
    for i in range(steps):
        mins = 540 + i * 5  # 9:00 시작, 5분 간격
        hh = mins // 60
        mm = mins % 60
        times.append(hh * 100 + mm)

    return list(zip(times, path))


def _calc_signals(closes, highs, lows, idx):
    """분봉 대용: 일봉 기반 스캘핑 시그널 강도 (0~100)

    RSI(4) 과매도 + EMA크로스 + 거래량 등을 단순화한 점수.
    """
    if idx < 20:
        return 0

    # RSI(4) — 과매도 반등
    rv = rsi(closes[:idx+1], 4)
    rsi_score = 0
    if rv[idx] < 25:
        rsi_score = 30
    elif rv[idx] < 35:
        rsi_score = 15

    # EMA(9) vs EMA(20) — 골든크로스
    ema9 = sma(closes[max(0,idx-8):idx+1], min(9, idx+1))
    ema20 = sma(closes[max(0,idx-19):idx+1], min(20, idx+1))
    ema_score = 0
    if ema9 and ema20 and ema9[-1] > ema20[-1]:
        ema_score = 20

    # 종가 > 20일선 (추세 확인)
    ma20 = sma(closes[:idx+1], 20)
    trend_score = 20 if ma20 and ma20[idx] > 0 and closes[idx] > ma20[idx] else 0

    # 전일 대비 하락 (눌림목)
    dip_score = 0
    if idx >= 1 and closes[idx] < closes[idx-1]:
        drop = (closes[idx] - closes[idx-1]) / closes[idx-1] * 100
        if -3 < drop < -0.5:
            dip_score = 15

    return min(100, rsi_score + ema_score + trend_score + dip_score)


def run_scalp_backtest(start, end, capital=1000000, max_pos=3,
                       min_price=5000, buy_end_hour=1500,
                       stop_loss_atr=1.0, take_profit_atr=1.5,
                       trailing_atr=1.2, signal_threshold=50,
                       max_daily_trades=120):
    """스캘핑 백테스트 — 일봉 기반 장중 시뮬레이션 (고정 자금)"""
    dates = [d for d in ALL_DATES if start <= d <= end]
    init_capital = capital
    cumulative_pnl = 0
    trades = []
    daily_stats = []

    for date in dates:
        day_trades = 0
        day_pnl = 0
        positions = []  # 당일 포지션 (초단타는 당일 청산)
        per_pos_budget = init_capital // max_pos  # 고정 자금 (복리 아님)

        # 당일 매매 가능 종목 선별
        candidates = []
        for sym, data in ALL_DATA.items():
            idx = data["_idx"].get(date)
            if idx is None or idx < 30:
                continue
            cl = data["closes"]
            hi = data["highs"]
            lo = data["lows"]
            op = data["opens"] if "opens" in data else cl  # opens 없으면 closes 사용

            if cl[idx] < min_price:
                continue

            # ATR 계산
            av = atr(hi[:idx+1], lo[:idx+1], cl[:idx+1], 14)
            if av[idx] <= 0:
                continue

            # 시그널 강도
            sig = _calc_signals(cl, hi, lo, idx)
            if sig < signal_threshold:
                continue

            o = op[idx] if hasattr(op, '__getitem__') else cl[idx]
            candidates.append({
                "s": sym, "o": o, "h": hi[idx], "l": lo[idx], "c": cl[idx],
                "atr": av[idx], "sig": sig,
            })

        # 시그널 강도순 정렬
        candidates.sort(key=lambda x: x["sig"], reverse=True)

        # 각 후보 종목에 대해 장중 시뮬레이션
        for cand in candidates:
            if day_trades >= max_daily_trades:
                break

            path = _gen_intraday_path(cand["o"], cand["h"], cand["l"], cand["c"])
            atr_val = cand["atr"]

            # 장중 매매 시뮬레이션
            in_position = False
            entry_price = 0
            entry_time = 0
            highest = 0

            for time_hhmm, price in path:
                if time_hhmm >= buy_end_hour and not in_position:
                    continue  # 매수 마감 시간 이후 신규 진입 차단

                if not in_position:
                    # 진입 조건: 시그널 있고 포지션 여유
                    if len(positions) < max_pos and day_trades < max_daily_trades:
                        # 간단한 진입: 전일종가 대비 눌림 후 반등
                        if price <= cand["o"] - atr_val * 0.3:  # ATR 0.3배 눌림
                            entry_price = price
                            entry_time = time_hhmm
                            highest = price
                            in_position = True
                            positions.append(cand["s"])

                elif in_position:
                    highest = max(highest, price)
                    pnl_pct = (price - entry_price) / entry_price * 100
                    trail_pct = (price - highest) / highest * 100 if highest > entry_price else 0

                    sell = False
                    reason = ""

                    # 손절
                    if price <= entry_price - atr_val * stop_loss_atr:
                        sell = True
                        reason = "stop"
                    # 트레일링 스탑
                    elif highest > entry_price + atr_val * 0.5 and price <= highest - atr_val * trailing_atr:
                        sell = True
                        reason = "trail"
                    # 익절
                    elif price >= entry_price + atr_val * take_profit_atr:
                        sell = True
                        reason = "tp"
                    # 장마감 청산 (15:18)
                    elif time_hhmm >= 1518:
                        sell = True
                        reason = "close"

                    if sell:
                        qty = per_pos_budget // entry_price if entry_price > 0 else 0
                        comm = (entry_price + price) * qty * COMM_PCT / 100
                        pnl_amount = int((price - entry_price) * qty - comm)

                        trades.append({
                            "date": date, "sym": cand["s"],
                            "entry": entry_price, "exit": price,
                            "pnl_pct": pnl_pct, "pnl": pnl_amount,
                            "reason": reason, "time": entry_time,
                            "exit_time": time_hhmm,
                        })

                        day_pnl += pnl_amount
                        day_trades += 1
                        cumulative_pnl += pnl_amount
                        in_position = False
                        if cand["s"] in positions:
                            positions.remove(cand["s"])

            # 장마감까지 미청산 (이미 path 마지막에서 close 처리됨)
            if in_position:
                price = cand["c"]
                pnl_pct = (price - entry_price) / entry_price * 100
                qty = per_pos_budget // entry_price if entry_price > 0 else 0
                comm = (entry_price + price) * qty * COMM_PCT / 100
                pnl_amount = int((price - entry_price) * qty - comm)
                trades.append({
                    "date": date, "sym": cand["s"],
                    "entry": entry_price, "exit": price,
                    "pnl_pct": pnl_pct, "pnl": pnl_amount,
                    "reason": "eod", "time": entry_time,
                    "exit_time": 1530,
                })
                day_pnl += pnl_amount
                day_trades += 1
                cumulative_pnl += pnl_amount

        if day_trades > 0:
            daily_stats.append({"date": date, "trades": day_trades, "pnl": day_pnl})

    return _summarize(trades, daily_stats, init_capital, init_capital + cumulative_pnl)


def _summarize(trades, daily_stats, init_cap, final_cap):
    if not trades:
        return {"n": 0, "wr": "0%", "ret": "+0.0%", "pf": "0.00",
                "avg_daily": 0, "avg_pnl": 0, "mdd": "0%"}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    wr = len(wins) / len(trades) * 100
    gw = sum(t["pnl"] for t in wins) if wins else 0
    gl = abs(sum(t["pnl"] for t in losses)) or 1
    pf = gw / gl
    ret = (final_cap - init_cap) / init_cap * 100

    # MDD
    peak = init_cap
    mdd = 0
    cum = init_cap
    for ds in daily_stats:
        cum += ds["pnl"]
        peak = max(peak, cum)
        dd = (cum - peak) / peak * 100
        mdd = min(mdd, dd)

    # 청산 사유별 분석
    by_reason = {}
    for t in trades:
        r = t["reason"]
        if r not in by_reason:
            by_reason[r] = {"n": 0, "w": 0, "pnl": 0}
        by_reason[r]["n"] += 1
        if t["pnl"] > 0:
            by_reason[r]["w"] += 1
        by_reason[r]["pnl"] += t["pnl"]

    # 시간대별 분석
    by_hour = {}
    for t in trades:
        h = t["time"] // 100
        if h not in by_hour:
            by_hour[h] = {"n": 0, "w": 0, "pnl": 0}
        by_hour[h]["n"] += 1
        if t["pnl"] > 0:
            by_hour[h]["w"] += 1
        by_hour[h]["pnl"] += t["pnl"]

    avg_daily = len(trades) / len(daily_stats) if daily_stats else 0
    avg_pnl = sum(t["pnl"] for t in trades) / len(trades)

    return {
        "n": len(trades), "wr": f"{wr:.1f}%",
        "ret": f"{ret:+.1f}%", "pf": f"{pf:.2f}",
        "mdd": f"{mdd:.1f}%",
        "avg_daily": f"{avg_daily:.1f}",
        "avg_pnl": f"{avg_pnl:+,.0f}",
        "total_pnl": f"{final_cap - init_cap:+,}",
        "by_reason": by_reason,
        "by_hour": by_hour,
    }


def _print_result(label, r):
    P(f"\n{'='*80}")
    P(f"  {label}")
    P(f"{'='*80}")
    P(f"  거래: {r['n']}건 | 승률: {r['wr']} | 수익률: {r['ret']} | PF: {r['pf']}")
    P(f"  MDD: {r['mdd']} | 일평균: {r['avg_daily']}건 | 건당평균: {r['avg_pnl']}원")
    P(f"  총 손익: {r['total_pnl']}원")

    P(f"\n  [청산 사유별]")
    for reason, v in sorted(r["by_reason"].items(), key=lambda x: x[1]["pnl"]):
        wr = v["w"] / v["n"] * 100 if v["n"] else 0
        P(f"    {reason:8} | {v['n']:4}건 | 승률 {wr:5.1f}% | {v['pnl']:+,}원")

    P(f"\n  [시간대별]")
    for h in sorted(r["by_hour"].keys()):
        v = r["by_hour"][h]
        wr = v["w"] / v["n"] * 100 if v["n"] else 0
        P(f"    {h:2}시 | {v['n']:4}건 | 승률 {wr:5.1f}% | {v['pnl']:+,}원")


# === 테스트 실행 ===
START, END = "20240101", "20260331"

P("\n" + "#"*80)
P("  스캘핑 전략 백테스트 — 필터 효과 비교 (3개월)")
P("#"*80)

# A) 기존 설정 (baseline)
P("\n>>> A) 기존 설정 (baseline) 실행 중...")
r_base = run_scalp_backtest(START, END, min_price=5000, buy_end_hour=1500,
                            stop_loss_atr=1.0, take_profit_atr=1.5)
_print_result("A) 기존 설정 (min_price=5K, 마감=15시, stop=ATR*1.0)", r_base)

# B) 시간 필터만 (13시 마감)
P("\n>>> B) 시간 필터 (13시 마감) 실행 중...")
r_time = run_scalp_backtest(START, END, min_price=5000, buy_end_hour=1300,
                            stop_loss_atr=1.0, take_profit_atr=1.5)
_print_result("B) 시간 필터 (min_price=5K, 마감=13시, stop=ATR*1.0)", r_time)

# C) 가격 필터만 (1만원)
P("\n>>> C) 가격 필터 (1만원) 실행 중...")
r_price = run_scalp_backtest(START, END, min_price=10000, buy_end_hour=1500,
                             stop_loss_atr=1.0, take_profit_atr=1.5)
_print_result("C) 가격 필터 (min_price=10K, 마감=15시, stop=ATR*1.0)", r_price)

# D) 손절 완화만 (ATR 1.5배)
P("\n>>> D) 손절 완화 (ATR*1.5) 실행 중...")
r_stop = run_scalp_backtest(START, END, min_price=5000, buy_end_hour=1500,
                            stop_loss_atr=1.5, take_profit_atr=2.0)
_print_result("D) 손절 완화 (min_price=5K, 마감=15시, stop=ATR*1.5, tp=ATR*2.0)", r_stop)

# E) 전체 필터 적용
P("\n>>> E) 전체 필터 적용 실행 중...")
r_all = run_scalp_backtest(START, END, min_price=10000, buy_end_hour=1300,
                           stop_loss_atr=1.5, take_profit_atr=2.0,
                           trailing_atr=1.5)
_print_result("E) 전체 필터 (min_price=10K, 마감=13시, stop=ATR*1.5, tp=ATR*2.0)", r_all)

# F) 시그널 강도 올리기 (60점)
P("\n>>> F) 시그널 강도 상향 (60점) + 전체 필터...")
r_sig = run_scalp_backtest(START, END, min_price=10000, buy_end_hour=1300,
                           stop_loss_atr=1.5, take_profit_atr=2.0,
                           trailing_atr=1.5, signal_threshold=60)
_print_result("F) 강한 시그널 (threshold=60, min_price=10K, 마감=13시, stop=ATR*1.5)", r_sig)

# === 비교표 ===
P("\n" + "="*80)
P("  종합 비교")
P("="*80)
P(f"{'설정':>25} {'거래':>6} {'승률':>7} {'수익률':>8} {'PF':>6} {'MDD':>7} {'일평균':>6} {'총손익':>12}")
P("-"*85)
for label, r in [("A) 기존", r_base), ("B) 시간필터", r_time), ("C) 가격필터", r_price),
                 ("D) 손절완화", r_stop), ("E) 전체필터", r_all), ("F) 강한시그널", r_sig)]:
    P(f"{label:>25} {r['n']:>6} {r['wr']:>7} {r['ret']:>8} {r['pf']:>6} {r['mdd']:>7} {r['avg_daily']:>6} {r['total_pnl']:>12}")

P("\n완료!")
