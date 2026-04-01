"""백테스트 엔진 — RSI(2) + Holy Grail 전략 과거 데이터 검증

사용법:
    python -m backtest.engine --start 20240101 --end 20241231
"""

import argparse
import logging
from datetime import datetime, timedelta
from collector.market_data import get_kospi200_symbols, get_kosdaq150_symbols, get_daily_ohlcv, get_trade_value
from strategy.indicators import sma, rsi, adx
import config

logger = logging.getLogger(__name__)


class BacktestEngine:
    def __init__(self, capital: int = config.MAIN_CAPITAL, max_positions: int = config.MAIN_MAX_POSITIONS):
        self.initial_capital = capital
        self.capital = capital
        self.max_positions = max_positions
        self.positions: list[dict] = []
        self.trades: list[dict] = []
        self.daily_pnl: dict[str, int] = {}  # {date: pnl}

    def run(self, start: str, end: str):
        """백테스트 실행 (start/end: YYYYMMDD)"""
        logger.info(f"백테스트 시작: {start} ~ {end}")

        # 종목 리스트 (현재 기준 — 생존자 편향 있음, 주의)
        symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
        logger.info(f"대상 종목: {len(symbols)}개")

        # 종목별 데이터 로드
        all_data = {}
        for sym in symbols:
            data = get_daily_ohlcv(sym, days=400)
            if data and len(data["closes"]) >= 200:
                all_data[sym] = data

        logger.info(f"데이터 로드 완료: {len(all_data)}종목")

        # 날짜 범위 생성
        dates = set()
        for data in all_data.values():
            dates.update(data["dates"])
        dates = sorted([d for d in dates if start <= d <= end])
        logger.info(f"거래일: {len(dates)}일")

        for i, date in enumerate(dates):
            self._process_day(date, i, dates, all_data)

        return self._summary()

    def _process_day(self, date: str, date_idx: int, dates: list[str], all_data: dict):
        """하루 시뮬레이션"""
        daily_pnl = 0

        # 1. 청산 체크 (보유 포지션)
        for pos in list(self.positions):
            sym = pos["symbol"]
            data = all_data.get(sym)
            if not data or date not in data["dates"]:
                continue

            idx = data["dates"].index(date)
            current_price = data["closes"][idx]
            entry_price = pos["entry_price"]
            pnl_pct = (current_price - entry_price) / entry_price * 100

            pos["high_price"] = max(pos.get("high_price", entry_price), current_price)
            trailing_pnl = (current_price - pos["high_price"]) / pos["high_price"] * 100

            reason = None

            # 고정 손절 -3%
            if pnl_pct <= config.MAIN_STOP_LOSS_PCT:
                reason = f"고정 손절 ({pnl_pct:.1f}%)"

            # 추적 손절 -2.5%
            elif trailing_pnl <= config.MAIN_TRAILING_STOP_PCT and pos["high_price"] > entry_price:
                reason = f"추적 손절 ({trailing_pnl:.1f}%)"

            # 최대 보유 기간
            elif self._hold_days(pos["entry_date"], date, [d for d in data["dates"]]) >= config.MAIN_MAX_HOLD_DAYS:
                reason = f"최대 보유 {config.MAIN_MAX_HOLD_DAYS}일"

            # RSI(2) > 70 익절
            elif idx >= 2:
                rsi_values = rsi(data["closes"][:idx + 1], config.RSI_PERIOD)
                if rsi_values[-1] > config.RSI_EXIT_THRESHOLD:
                    reason = f"RSI 익절 (RSI={rsi_values[-1]:.1f})"

            if reason:
                cost = entry_price * pos["qty"]
                pnl = int(current_price * pos["qty"] - cost)
                daily_pnl += pnl
                self.capital += cost + pnl  # 원금 회수 + 손익
                self.trades.append({
                    "symbol": sym,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "qty": pos["qty"],
                    "pnl": pnl,
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": reason,
                })
                self.positions = [p for p in self.positions if p["symbol"] != sym]

        # 2. 신규 진입 스크리닝
        if len(self.positions) >= self.max_positions:
            return

        candidates = []
        for sym, data in all_data.items():
            if date not in data["dates"]:
                continue
            if any(p["symbol"] == sym for p in self.positions):
                continue

            idx = data["dates"].index(date)
            if idx < 200:
                continue

            closes = data["closes"][:idx + 1]
            highs = data["highs"][:idx + 1]
            lows = data["lows"][:idx + 1]
            last_close = closes[-1]

            if last_close < config.MIN_PRICE:
                continue

            ma200 = sma(closes, config.MA_LONG)
            if ma200[-1] == 0 or last_close <= ma200[-1]:
                continue

            adx_values = adx(highs, lows, closes, config.ADX_PERIOD)
            if adx_values[-1] < config.ADX_MIN:
                continue

            rsi_values = rsi(closes, config.RSI_PERIOD)
            if rsi_values[-1] >= config.RSI_ENTRY_THRESHOLD:
                continue

            candidates.append({
                "symbol": sym,
                "close": last_close,
                "rsi2": rsi_values[-1],
            })

        # RSI 가장 낮은 순
        candidates.sort(key=lambda x: x["rsi2"])
        available = self.max_positions - len(self.positions)

        for c in candidates[:available]:
            budget = self.capital // self.max_positions
            qty = budget // c["close"]
            if qty <= 0:
                continue
            cost = c["close"] * qty
            self.capital -= cost  # 매수 금액 차감
            self.positions.append({
                "symbol": c["symbol"],
                "qty": qty,
                "entry_price": c["close"],
                "high_price": c["close"],
                "entry_date": date,
            })

        self.daily_pnl[date] = daily_pnl

    def _hold_days(self, entry_date: str, current_date: str, all_dates: list[str]) -> int:
        """거래일 기준 보유일수"""
        if entry_date not in all_dates or current_date not in all_dates:
            return 0
        start_idx = all_dates.index(entry_date)
        end_idx = all_dates.index(current_date)
        return end_idx - start_idx

    def _summary(self) -> dict:
        """백테스트 결과 요약"""
        if not self.trades:
            return {"error": "거래 없음"}

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] < 0]
        total_pnl = sum(t["pnl"] for t in self.trades)
        win_rate = len(wins) / len(self.trades) * 100 if self.trades else 0

        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses else float("inf")

        # 최대 낙폭 (MDD)
        cumulative = 0
        peak = 0
        max_dd = 0
        for date in sorted(self.daily_pnl.keys()):
            cumulative += self.daily_pnl[date]
            peak = max(peak, cumulative)
            dd = cumulative - peak
            max_dd = min(max_dd, dd)

        # 일평균 손익
        trading_days = len([d for d in self.daily_pnl.values()])
        avg_daily = total_pnl / trading_days if trading_days > 0 else 0

        result = {
            "총 거래": len(self.trades),
            "승률": f"{win_rate:.1f}%",
            "총 손익": f"{total_pnl:+,}원",
            "평균 수익(승)": f"{avg_win:+,.0f}원",
            "평균 손실(패)": f"{avg_loss:+,.0f}원",
            "Profit Factor": f"{profit_factor:.2f}",
            "최대 낙폭(MDD)": f"{max_dd:+,}원",
            "거래일수": trading_days,
            "일평균 손익": f"{avg_daily:+,.0f}원",
            "최종 자본": f"{self.capital:,}원",
            "수익률": f"{(self.capital - self.initial_capital) / self.initial_capital * 100:+.1f}%",
        }
        return result

    def print_report(self, result: dict):
        """결과 출력"""
        print("\n" + "=" * 50)
        print("  RSI(2) + Holy Grail 백테스트 결과")
        print("=" * 50)
        for k, v in result.items():
            print(f"  {k:20s}: {v}")
        print("=" * 50)

        # 최근 10건 거래
        if self.trades:
            print("\n최근 10건 거래:")
            for t in self.trades[-10:]:
                print(f"  {t['symbol']} {t['entry_date']}→{t['exit_date']} "
                      f"{t['pnl']:+,}원 ({t['pnl_pct']:+.1f}%) [{t['reason']}]")


def main():
    parser = argparse.ArgumentParser(description="RSI(2) 백테스트")
    parser.add_argument("--start", default="20240101", help="시작일 (YYYYMMDD)")
    parser.add_argument("--end", default="20241231", help="종료일 (YYYYMMDD)")
    parser.add_argument("--capital", type=int, default=config.MAIN_CAPITAL, help="초기 자본")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    engine = BacktestEngine(capital=args.capital)
    result = engine.run(args.start, args.end)
    engine.print_report(result)


if __name__ == "__main__":
    main()
