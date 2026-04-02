"""백테스트 엔진 — 미국 박스권 매매 전략

사용법:
    python -m backtest.us_box_engine --start 20230101 --end 20251231
    python -m backtest.us_box_engine --symbols TQQQ,SOXL,COIN --start 20240101
"""

import argparse
import logging
from datetime import datetime
from collector.us_market_data import bulk_download, get_us_daily_ohlcv
from collector.us_universe import load_universe
from strategy.indicators import adx, atr, rsi, find_support_resistance, box_position_pct
import config

logger = logging.getLogger(__name__)


class USBoxBacktestEngine:
    def __init__(self, capital: float = 300_000, max_positions: int = 3,
                 commission_pct: float = 0.35):
        """
        capital: 초기 자본 (원화)
        commission_pct: 편도 수수료 % (KIS 해외주식 ~0.25% + 스프레드)
        """
        self.initial_capital = capital
        self.capital = capital
        self.max_positions = max_positions
        self.commission_pct = commission_pct
        self.positions: list[dict] = []
        self.trades: list[dict] = []
        self.daily_equity: dict[str, float] = {}
        self.exchange_rate = config.EXCHANGE_RATE_USD_KRW

    def run(self, symbols: list[str], start: str, end: str, lookback: int = 60) -> dict:
        """백테스트 실행"""
        logger.info(f"백테스트: {len(symbols)}종목, {start}~{end}, lookback={lookback}일")

        # 데이터 수집 (yfinance)
        all_data = bulk_download(symbols, days=400)
        logger.info(f"데이터 로드: {len(all_data)}종목")

        # 전체 거래일 추출
        dates = set()
        for data in all_data.values():
            dates.update(data.get("dates", []))
        dates = sorted(d for d in dates if start <= d <= end)
        logger.info(f"거래일: {len(dates)}일")

        for i, date in enumerate(dates):
            self._process_day(date, i, dates, all_data, lookback)

        return self._summary()

    def _process_day(self, date: str, date_idx: int, dates: list[str],
                      all_data: dict, lookback: int):
        """하루 시뮬레이션"""

        # 1. 청산 체크
        for pos in list(self.positions):
            sym = pos["symbol"]
            data = all_data.get(sym)
            if not data or date not in data["dates"]:
                continue

            idx = data["dates"].index(date)
            current = data["closes"][idx]
            entry = pos["entry_price"]
            support = pos["support"]
            resistance = pos["resistance"]
            atr_val = pos["atr"]
            pnl_pct = (current - entry) / entry * 100

            reason = None

            # 하드 스톱
            hard_stop = support - config.US_BOX_HARD_STOP_ATR * atr_val
            if current <= hard_stop:
                reason = f"하드 스톱 (${current:.2f} <= ${hard_stop:.2f})"

            # 소프트 스톱 (종가 2일 연속)
            if not reason:
                soft_stop = support - config.US_BOX_SOFT_STOP_ATR * atr_val
                if current <= soft_stop:
                    pos["soft_stop_days"] = pos.get("soft_stop_days", 0) + 1
                    if pos["soft_stop_days"] >= 2:
                        reason = f"소프트 스톱 2일 ({current:.2f})"
                else:
                    pos["soft_stop_days"] = 0

            # 익절
            take_profit = resistance * (1 - config.US_BOX_TAKE_PROFIT_PCT / 100)
            if not reason and current >= take_profit:
                reason = f"익절 (${current:.2f} >= ${take_profit:.2f})"

            # 최대 보유 30일
            hold_days = self._hold_days(pos["entry_date"], date, data["dates"])
            if not reason and hold_days >= 30:
                reason = "최대 보유 30일"

            if reason:
                commission = entry * pos["qty"] * self.commission_pct / 100  # 매수 커미션
                commission += current * pos["qty"] * self.commission_pct / 100  # 매도 커미션
                pnl_usd = (current - entry) * pos["qty"] - commission
                pnl_krw = pnl_usd * self.exchange_rate
                self.capital += pos["cost_krw"] + pnl_krw

                self.trades.append({
                    "symbol": sym,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": entry,
                    "exit_price": current,
                    "qty": pos["qty"],
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_krw": int(pnl_krw),
                    "pnl_pct": round(pnl_pct, 2),
                    "commission": round(commission, 2),
                    "reason": reason,
                    "hold_days": hold_days,
                })
                self.positions = [p for p in self.positions if p["symbol"] != sym]

        # 2. 신규 진입 스캔
        if len(self.positions) >= self.max_positions:
            self._record_equity(date, all_data)
            return

        for sym, data in all_data.items():
            if len(self.positions) >= self.max_positions:
                break
            if date not in data["dates"]:
                continue
            if any(p["symbol"] == sym for p in self.positions):
                continue

            idx = data["dates"].index(date)
            if idx < lookback:
                continue

            closes = data["closes"][:idx + 1]
            highs = data["highs"][:idx + 1]
            lows = data["lows"][:idx + 1]
            volumes = data["volumes"][:idx + 1]
            last_close = closes[-1]

            if last_close < config.US_MIN_PRICE:
                continue

            # 거래량 필터
            avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
            if avg_vol < config.US_MIN_AVG_VOLUME:
                continue

            # ADX < 20
            adx_values = adx(highs, lows, closes, 14)
            if adx_values[-1] <= 0 or adx_values[-1] >= config.US_BOX_ADX_MAX:
                continue

            # 지지/저항
            sr = find_support_resistance(
                highs[-lookback:], lows[-lookback:], closes[-lookback:],
                tolerance=config.US_BOX_TOUCH_TOLERANCE,
            )
            if not sr:
                continue
            if sr["box_width_pct"] < config.US_BOX_MIN_WIDTH_PCT:
                continue
            if sr["box_width_pct"] > config.US_BOX_MAX_WIDTH_PCT:
                continue
            if sr["support_touches"] < config.US_BOX_MIN_TOUCHES:
                continue
            if sr["resistance_touches"] < config.US_BOX_MIN_TOUCHES:
                continue

            # Buy Zone (지지선 이탈 제외)
            bp = box_position_pct(last_close, sr["support"], sr["resistance"])
            if bp < 0 or bp > config.US_BOX_BUY_ZONE_PCT:
                continue

            # 반등 시그널 (간소화: 양봉 + RSI 상승)
            signals = 0
            if closes[-1] > data["opens"][idx]:
                signals += 1
            rsi_vals = rsi(closes, 14)
            if len(rsi_vals) >= 2 and 25 <= rsi_vals[-1] <= 45 and rsi_vals[-1] > rsi_vals[-2]:
                signals += 1
            if avg_vol > 0 and volumes[-1] >= avg_vol * 1.2:
                signals += 1
            if signals < config.US_BOX_SIGNAL_MIN:
                continue

            # ATR
            atr_values = atr(highs, lows, closes, 14)
            atr_val = atr_values[-1]

            # 포지션 사이징
            budget_krw = self.capital / self.max_positions
            budget_usd = budget_krw / self.exchange_rate
            qty = int(budget_usd / last_close)
            if qty <= 0:
                continue

            cost_usd = last_close * qty
            cost_krw = cost_usd * self.exchange_rate
            if cost_krw > self.capital:
                continue

            self.capital -= cost_krw
            self.positions.append({
                "symbol": sym,
                "qty": qty,
                "entry_price": last_close,
                "entry_date": date,
                "support": sr["support"],
                "resistance": sr["resistance"],
                "atr": atr_val,
                "cost_krw": cost_krw,
                "soft_stop_days": 0,
            })

        self._record_equity(date, all_data)

    def _record_equity(self, date: str, all_data: dict):
        """일별 자산 기록"""
        equity = self.capital
        for pos in self.positions:
            data = all_data.get(pos["symbol"])
            if data and date in data["dates"]:
                idx = data["dates"].index(date)
                current = data["closes"][idx]
                equity += current * pos["qty"] * self.exchange_rate
            else:
                equity += pos["cost_krw"]  # 시세 없으면 원가
        self.daily_equity[date] = equity

    def _hold_days(self, entry_date: str, current_date: str, all_dates: list[str]) -> int:
        if entry_date not in all_dates or current_date not in all_dates:
            return 0
        return all_dates.index(current_date) - all_dates.index(entry_date)

    def _summary(self) -> dict:
        if not self.trades:
            return {"error": "거래 없음"}

        wins = [t for t in self.trades if t["pnl_krw"] > 0]
        losses = [t for t in self.trades if t["pnl_krw"] < 0]
        total_pnl = sum(t["pnl_krw"] for t in self.trades)
        total_commission = sum(t["commission"] for t in self.trades)
        win_rate = len(wins) / len(self.trades) * 100

        avg_win = sum(t["pnl_krw"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl_krw"] for t in losses) / len(losses) if losses else 0
        gross_win = sum(t["pnl_krw"] for t in wins)
        gross_loss = abs(sum(t["pnl_krw"] for t in losses))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        avg_hold = sum(t["hold_days"] for t in self.trades) / len(self.trades)

        # MDD
        equity_list = sorted(self.daily_equity.items())
        peak = 0
        max_dd = 0
        max_dd_pct = 0
        for _, eq in equity_list:
            peak = max(peak, eq)
            dd = eq - peak
            dd_pct = dd / peak * 100 if peak > 0 else 0
            if dd < max_dd:
                max_dd = dd
                max_dd_pct = dd_pct

        final_equity = equity_list[-1][1] if equity_list else self.initial_capital

        # 청산 이유별 통계
        reasons = {}
        for t in self.trades:
            r = t["reason"].split("(")[0].strip()
            reasons[r] = reasons.get(r, 0) + 1

        return {
            "총 거래": len(self.trades),
            "승률": f"{win_rate:.1f}%",
            "총 손익": f"{total_pnl:+,}원",
            "총 수수료": f"{total_commission * self.exchange_rate:+,.0f}원",
            "평균 수익(승)": f"{avg_win:+,.0f}원",
            "평균 손실(패)": f"{avg_loss:+,.0f}원",
            "Profit Factor": f"{profit_factor:.2f}",
            "평균 보유일": f"{avg_hold:.1f}일",
            "MDD": f"{max_dd:+,.0f}원 ({max_dd_pct:.1f}%)",
            "최종 자산": f"{final_equity:,.0f}원",
            "수익률": f"{(final_equity - self.initial_capital) / self.initial_capital * 100:+.1f}%",
            "청산 이유": reasons,
        }

    def print_report(self, result: dict):
        print("\n" + "=" * 55)
        print("  US Box Trading 백테스트 결과")
        print("=" * 55)
        for k, v in result.items():
            print(f"  {k:20s}: {v}")
        print("=" * 55)

        if self.trades:
            print(f"\n최근 10건 거래:")
            for t in self.trades[-10:]:
                print(
                    f"  {t['symbol']:6s} {t['entry_date']}→{t['exit_date']} "
                    f"${t['entry_price']:.2f}→${t['exit_price']:.2f} "
                    f"{t['pnl_krw']:+,}원 ({t['pnl_pct']:+.1f}%) [{t['reason']}]"
                )


def main():
    parser = argparse.ArgumentParser(description="US Box Trading 백테스트")
    parser.add_argument("--start", default="20240101", help="시작일 (YYYYMMDD)")
    parser.add_argument("--end", default="20261231", help="종료일 (YYYYMMDD)")
    parser.add_argument("--capital", type=int, default=300_000, help="초기 자본 (원)")
    parser.add_argument("--symbols", default="", help="종목 (쉼표 구분, 비우면 유니버스)")
    parser.add_argument("--lookback", type=int, default=60, help="박스권 판별 기간")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        universe = load_universe()
        if not universe:
            print("유니버스 파일이 없습니다. 먼저 봇을 실행하여 유니버스를 구축하세요.")
            return
        symbols = [u["symbol"] for u in universe]

    engine = USBoxBacktestEngine(capital=args.capital)
    result = engine.run(symbols, args.start, args.end, lookback=args.lookback)
    engine.print_report(result)


if __name__ == "__main__":
    main()
