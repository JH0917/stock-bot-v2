"""주전략: 상대강도 로테이션

워크포워드 검증 결과:
  - 상대강도(10d,T3,R5): 평균 +40.6%/3개월, 양수구간 85%, PF 1.79
  - 5거래일마다 리밸런싱, 10일 수익률 상위 3종목 집중

진입: 10일 수익률 상위 3종목 (양수만)
청산: 리밸런싱 시 상위에서 탈락 or -5% 손절
리밸런싱: 5거래일마다
"""

import logging
from datetime import datetime, timedelta
from collector.market_data import (
    get_kospi200_symbols,
    get_kosdaq150_symbols,
    get_daily_ohlcv,
)
import config

logger = logging.getLogger(__name__)

# ── 상대강도 전략 설정 ──
RS_LOOKBACK = 10        # 수익률 계산 기간 (거래일)
RS_TOP_N = 3            # 상위 N종목 보유
RS_REBAL_DAYS = 5       # 리밸런싱 주기 (거래일)
RS_STOP_LOSS_PCT = -5.0 # 개별 종목 손절


class RelativeStrengthStrategy:
    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor
        self._last_rebal_date = None
        self._trading_day_count = 0

    def _is_rebal_day(self) -> bool:
        """오늘이 리밸런싱 날인지 확인"""
        today = datetime.now().strftime("%Y%m%d")
        if self._last_rebal_date == today:
            return False  # 오늘 이미 리밸런싱 완료

        self._trading_day_count += 1
        if self._trading_day_count % RS_REBAL_DAYS == 1:
            return True
        return False

    async def scan_and_rank(self) -> list[dict]:
        """전 종목 스캔 → 10일 수익률 상위 3종목 선정"""
        symbols = get_kospi200_symbols() + get_kosdaq150_symbols()
        logger.info(f"[RS] 상대강도 스캔: {len(symbols)}종목")

        scores = []
        for sym in symbols:
            data = get_daily_ohlcv(sym, days=30)
            if not data or len(data["closes"]) < RS_LOOKBACK + 1:
                continue

            closes = data["closes"]
            last_close = closes[-1]

            # 최소 주가 필터
            if last_close < config.MIN_PRICE:
                continue

            # 10일 수익률 계산
            past_close = closes[-(RS_LOOKBACK + 1)]
            if past_close <= 0:
                continue
            ret = (last_close - past_close) / past_close * 100

            # 양수 수익률만
            if ret <= 0:
                continue

            scores.append({
                "symbol": sym,
                "close": last_close,
                "return_pct": round(ret, 2),
            })

        # 수익률 내림차순 정렬 → 상위 N
        scores.sort(key=lambda x: x["return_pct"], reverse=True)
        result = scores[:RS_TOP_N]

        logger.info(f"[RS] 상대강도 상위 {len(result)}종목:")
        for c in result:
            logger.info(f"  {c['symbol']} | 종가={c['close']:,} | 10일수익률={c['return_pct']:+.1f}%")
        return result

    async def rebalance(self):
        """리밸런싱 실행: 탈락 종목 매도 → 신규 종목 매수"""
        if not self._is_rebal_day():
            logger.info("[RS] 리밸런싱 아님 (주기 미도달)")
            return

        today = datetime.now().strftime("%Y%m%d")
        self._last_rebal_date = today

        # 1. 상위 종목 스캔
        targets = await self.scan_and_rank()
        target_symbols = set(c["symbol"] for c in targets)

        # 2. 현재 보유 중인 RS 포지션
        positions = self.risk_manager.get_positions(strategy="rs")
        held_symbols = set(p["symbol"] for p in positions)

        # 3. 탈락 종목 매도 (상위에서 빠진 종목)
        to_sell = held_symbols - target_symbols
        for pos in positions:
            if pos["symbol"] not in to_sell:
                continue
            current = await self.executor.get_current_price(pos["symbol"])
            if current <= 0:
                continue
            pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100
            await self._sell_position(pos, f"리밸런싱 탈락 (10d수익률 하위)", pnl_pct)

        # 4. 신규 매수 (상위인데 아직 미보유)
        positions = self.risk_manager.get_positions(strategy="rs")  # 매도 후 갱신
        held_symbols = set(p["symbol"] for p in positions)
        to_buy = [c for c in targets if c["symbol"] not in held_symbols]

        available_slots = RS_TOP_N - len(positions)
        for c in to_buy[:available_slots]:
            symbol = c["symbol"]
            budget = config.MAIN_CAPITAL // RS_TOP_N
            qty = budget // c["close"]
            if qty <= 0:
                logger.warning(f"[RS] {symbol} 매수 수량 0 (주가 {c['close']:,}원)")
                continue

            result = await self.executor.buy(symbol, qty)
            if result:
                self.risk_manager.add_position(
                    symbol=symbol,
                    qty=qty,
                    entry_price=c["close"],
                    strategy="rs",
                    entry_date=today,
                )
                logger.info(f"[RS] 매수: {symbol} {qty}주 @ {c['close']:,}원 | 10d수익률={c['return_pct']:+.1f}%")

        logger.info(f"[RS] 리밸런싱 완료 — 보유: {[p['symbol'] for p in self.risk_manager.get_positions(strategy='rs')]}")

    async def check_exit(self):
        """장중 손절 체크 (1분마다)"""
        positions = self.risk_manager.get_positions(strategy="rs")

        for pos in positions:
            symbol = pos["symbol"]
            current = await self.executor.get_current_price(symbol)
            if current <= 0:
                continue

            pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100

            # 손절 -5%
            if pnl_pct <= RS_STOP_LOSS_PCT:
                await self._sell_position(pos, f"손절 ({pnl_pct:.1f}%)", pnl_pct)

    async def _sell_position(self, pos: dict, reason: str, pnl_pct: float):
        """포지션 매도"""
        symbol = pos["symbol"]
        qty = pos["qty"]
        result = await self.executor.sell(symbol, qty)
        if result:
            pnl = int(pos["entry_price"] * qty * pnl_pct / 100)
            self.risk_manager.close_position(symbol, pnl, reason, strategy="rs")
            logger.info(f"[RS] 매도: {symbol} {qty}주 | {reason} | 손익 {pnl:+,}원 ({pnl_pct:+.1f}%)")
