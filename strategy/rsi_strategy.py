"""주전략: Connors RSI(2) + Holy Grail 평균회귀

진입: RSI(2) < 5 + ADX(14) > 30 + 종가 > 200MA
청산: RSI(2) > 70 또는 최대 5일 보유
손절: -3% 고정 / -2.5% 추적
"""

import logging
from datetime import datetime, timedelta
from strategy.screener import screen_rsi_candidates, check_rsi_exit
import config

logger = logging.getLogger(__name__)


class RSIStrategy:
    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor

    async def scan_entry(self) -> list[dict]:
        """매수 후보 스캔 (장 마감 후 실행, 다음날 09:05 매수)"""
        if not self.risk_manager.can_open_main_position():
            logger.info("[RSI] 신규 진입 불가 (리스크 한도 또는 최대 포지션)")
            return []

        candidates = screen_rsi_candidates()
        if not candidates:
            logger.info("[RSI] 매수 후보 없음 (RSI(2) < 5 종목 없음)")
            return []

        # 쿨다운 필터 (최근 손절 종목 제외)
        filtered = [
            c for c in candidates
            if not self.risk_manager.is_in_cooldown(c["symbol"])
        ]

        # 최대 보유 가능 종목 수만큼만
        available_slots = config.MAIN_MAX_POSITIONS - self.risk_manager.main_position_count()
        return filtered[:available_slots]

    async def execute_entry(self, candidates: list[dict]):
        """매수 실행 (09:05 시장가)"""
        for c in candidates:
            symbol = c["symbol"]
            budget = config.MAIN_CAPITAL // config.MAIN_MAX_POSITIONS
            qty = budget // c["close"]
            if qty <= 0:
                logger.warning(f"[RSI] {symbol} 매수 수량 0 (주가 {c['close']:,}원)")
                continue

            result = await self.executor.buy(symbol, qty)
            if result:
                self.risk_manager.add_position(
                    symbol=symbol,
                    qty=qty,
                    entry_price=c["close"],
                    strategy="rsi",
                    entry_date=datetime.now().strftime("%Y%m%d"),
                )
                logger.info(f"[RSI] 매수 완료: {symbol} {qty}주 @ {c['close']:,}원")

    async def check_exit(self):
        """청산 조건 확인 (1분마다 실행)"""
        positions = self.risk_manager.get_positions(strategy="rsi")

        for pos in positions:
            symbol = pos["symbol"]
            entry_price = pos["entry_price"]

            # 현재가 조회
            current_price = await self.executor.get_current_price(symbol)
            if current_price <= 0:
                continue

            pnl_pct = (current_price - entry_price) / entry_price * 100
            pos["high_price"] = max(pos.get("high_price", entry_price), current_price)
            trailing_pnl = (current_price - pos["high_price"]) / pos["high_price"] * 100

            reason = None

            # 1. 고정 손절 -3%
            if pnl_pct <= config.MAIN_STOP_LOSS_PCT:
                reason = f"고정 손절 ({pnl_pct:.1f}%)"

            # 2. 추적 손절 -2.5% (최고점 대비)
            elif trailing_pnl <= config.MAIN_TRAILING_STOP_PCT and pos["high_price"] > entry_price:
                reason = f"추적 손절 (최고점 대비 {trailing_pnl:.1f}%)"

            # 3. 최대 보유 기간 초과
            elif self._hold_days(pos) >= config.MAIN_MAX_HOLD_DAYS:
                reason = f"최대 보유 {config.MAIN_MAX_HOLD_DAYS}일 도달"

            # 4. RSI(2) > 70 익절은 check_rsi_exit_all()에서 장 마감 후 1회 체크

            if reason:
                await self._sell_position(pos, reason, pnl_pct)

    async def _sell_position(self, pos: dict, reason: str, pnl_pct: float):
        """포지션 매도"""
        symbol = pos["symbol"]
        qty = pos["qty"]
        result = await self.executor.sell(symbol, qty)
        if result:
            pnl = int(pos["entry_price"] * qty * pnl_pct / 100)
            self.risk_manager.close_position(symbol, pnl, reason, strategy="rsi")
            logger.info(f"[RSI] 매도: {symbol} {qty}주 | {reason} | 손익 {pnl:+,}원 ({pnl_pct:+.1f}%)")

    async def check_rsi_exit_all(self):
        """장 마감 후 RSI(2) > 70 익절 체크 (15:35 1회 호출)
        조건 충족 종목은 다음날 09:05에 매도 예약
        """
        positions = self.risk_manager.get_positions(strategy="rsi")
        for pos in positions:
            if check_rsi_exit(pos["symbol"]):
                current = await self.executor.get_current_price(pos["symbol"])
                pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100 if current > 0 else 0
                logger.info(f"[RSI] {pos['symbol']} RSI(2)>{config.RSI_EXIT_THRESHOLD} — 다음날 매도 예정")
                # 다음날 매도를 위해 플래그 설정
                pos["exit_signal"] = True

    async def execute_rsi_exit(self):
        """09:05 — RSI 익절 매도 실행"""
        positions = self.risk_manager.get_positions(strategy="rsi")
        for pos in [p for p in positions if p.get("exit_signal")]:
            current = await self.executor.get_current_price(pos["symbol"])
            pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100 if current > 0 else 0
            await self._sell_position(pos, f"RSI(2) > {config.RSI_EXIT_THRESHOLD} 익절", pnl_pct)

    def _hold_days(self, pos: dict) -> int:
        """보유 거래일 수 계산 (주말 제외)"""
        entry = datetime.strptime(pos["entry_date"], "%Y%m%d")
        days = 0
        current = entry
        while current < datetime.now():
            current += timedelta(days=1)
            if current.weekday() < 5:  # 월~금
                days += 1
        return days
