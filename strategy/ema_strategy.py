"""주전략: EMA(13/21) 크로스 + RSI(14)>50 트렌드 추종

진입: EMA(13) > EMA(21) 골든크로스 + RSI(14) > 50
청산: 데드크로스 / 익절 +8% / 추적손절 -2.5% / 고정손절 -4% / 최대 10일 보유

워크포워드 검증(14개월): +207%, PF=1.98, MDD=-9.1%, 100% 양수 윈도우
"""

import logging
from datetime import datetime, timedelta
from strategy.screener import screen_ema_candidates
import config

logger = logging.getLogger(__name__)


class EMAStrategy:
    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor

    async def scan_entry(self) -> list[dict]:
        """매수 후보 스캔 (09:05 실행)"""
        if not self.risk_manager.can_open_main_position():
            logger.info("[EMA] 신규 진입 불가 (리스크 한도 또는 최대 포지션)")
            return []

        candidates = screen_ema_candidates()
        if not candidates:
            logger.info("[EMA] 매수 후보 없음")
            return []

        # 쿨다운 필터 (최근 손절 종목 제외)
        filtered = [
            c for c in candidates
            if not self.risk_manager.is_in_cooldown(c["symbol"])
        ]

        return filtered

    async def execute_entry(self, candidates: list[dict]):
        """매수 실행 (09:05 시장가)"""
        for c in candidates:
            if self.risk_manager.main_position_count() >= config.MAIN_MAX_POSITIONS:
                logger.info("[EMA] 최대 포지션 도달 — 매수 중단")
                break

            symbol = c["symbol"]
            budget = config.MAIN_CAPITAL // config.MAIN_MAX_POSITIONS
            qty = budget // c["close"]
            if qty <= 0:
                logger.warning(f"[EMA] {symbol} 매수 수량 0 (주가 {c['close']:,}원)")
                continue

            result = await self.executor.buy(symbol, qty)
            if result:
                self.risk_manager.add_position(
                    symbol=symbol,
                    qty=qty,
                    entry_price=c["close"],
                    strategy="ema",
                    entry_date=datetime.now().strftime("%Y%m%d"),
                )
                logger.info(f"[EMA] 매수 완료: {symbol} {qty}주 @ {c['close']:,}원")

    async def check_exit(self):
        """청산 조건 확인 (1분마다 실행)"""
        positions = self.risk_manager.get_positions(strategy="ema")
        dirty = False

        for pos in positions:
            symbol = pos["symbol"]
            entry_price = pos["entry_price"]

            current_price = await self.executor.get_current_price(symbol)
            if current_price <= 0:
                continue

            pnl_pct = (current_price - entry_price) / entry_price * 100
            old_high = pos.get("high_price", entry_price)
            pos["high_price"] = max(old_high, current_price)
            if pos["high_price"] != old_high:
                dirty = True
            trailing_pnl = (current_price - pos["high_price"]) / pos["high_price"] * 100

            logger.info(f"[EMA] {symbol} 현재가 {current_price:,} | "
                        f"수익률 {pnl_pct:+.1f}% | 최고가 {pos['high_price']:,} | "
                        f"추적 {trailing_pnl:+.1f}% | 보유 {self._hold_days(pos)}일")

            reason = None

            # 1. 고정 손절
            if pnl_pct <= config.MAIN_STOP_LOSS_PCT:
                reason = f"고정 손절 ({pnl_pct:.1f}%)"

            # 2. 추적 손절 (최고점 대비)
            elif trailing_pnl <= config.MAIN_TRAILING_STOP_PCT and pos["high_price"] > entry_price:
                reason = f"추적 손절 (최고점 대비 {trailing_pnl:.1f}%)"

            # 3. 익절 목표
            elif pnl_pct >= config.MAIN_TARGET_PROFIT_PCT:
                reason = f"익절 목표 도달 ({pnl_pct:.1f}%)"

            # 4. 최대 보유 기간 초과
            elif self._hold_days(pos) >= config.MAIN_MAX_HOLD_DAYS:
                reason = f"최대 보유 {config.MAIN_MAX_HOLD_DAYS}일 도달"

            if reason:
                await self._sell_position(pos, reason, pnl_pct)

        if dirty:
            self.risk_manager._save()

    async def check_dead_cross_exit(self):
        """장 마감 후 데드크로스 체크 (15:35 1회 호출)"""
        from strategy.screener import check_ema_dead_cross

        positions = self.risk_manager.get_positions(strategy="ema")
        for pos in positions:
            if check_ema_dead_cross(pos["symbol"]):
                current = await self.executor.get_current_price(pos["symbol"])
                pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100 if current > 0 else 0
                logger.info(f"[EMA] {pos['symbol']} 데드크로스 감지 — 다음날 매도 예정")
                pos["exit_signal"] = True

    async def execute_dead_cross_exit(self):
        """09:05 — 데드크로스 매도 실행"""
        positions = self.risk_manager.get_positions(strategy="ema")
        for pos in [p for p in positions if p.get("exit_signal")]:
            current = await self.executor.get_current_price(pos["symbol"])
            pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100 if current > 0 else 0
            await self._sell_position(pos, "EMA 데드크로스 청산", pnl_pct)

    async def _sell_position(self, pos: dict, reason: str, pnl_pct: float):
        """포지션 매도"""
        symbol = pos["symbol"]
        qty = pos["qty"]
        result = await self.executor.sell(symbol, qty)
        if result:
            pnl = int(pos["entry_price"] * qty * pnl_pct / 100)
            self.risk_manager.close_position(symbol, pnl, reason, strategy="ema")
            logger.info(f"[EMA] 매도: {symbol} {qty}주 | {reason} | 손익 {pnl:+,}원 ({pnl_pct:+.1f}%)")

    def _hold_days(self, pos: dict) -> int:
        """보유 거래일 수 계산 (주말 제외)"""
        entry = datetime.strptime(pos["entry_date"], "%Y%m%d")
        days = 0
        current = entry
        while current < datetime.now():
            current += timedelta(days=1)
            if current.weekday() < 5:
                days += 1
        return days
