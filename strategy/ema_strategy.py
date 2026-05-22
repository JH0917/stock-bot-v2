"""주전략: EMA(13/21) 크로스 + Chandelier Exit 트렌드 추종

진입: EMA(13) > EMA(21) 골든크로스 + RSI(14) > 50
청산: 고정손절 -6% / Chandelier Exit(ATR14×3) / EMA 데드크로스
     익절 제한 없음, 보유일 제한 없음 → 추세를 끝까지 추종

백테스트(4년): +138.8%, 월평균 +3.1%, 승률24%, 페이오프비 6.1x
"""

import logging
from datetime import datetime, timedelta
from strategy.screener import screen_ema_candidates
from strategy.indicators import atr as calc_atr
from collector.market_data import get_daily_ohlcv
import config

logger = logging.getLogger(__name__)


class EMAStrategy:
    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor
        self._atr_cache = {}  # {symbol_YYYYMMDD: atr_value}

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
        """매수 실행 (09:05 시장가) — 실제 잔액 기반 동적 배분"""
        for c in candidates:
            current_pos = self.risk_manager.main_position_count()
            if current_pos >= config.MAIN_MAX_POSITIONS:
                logger.info("[EMA] 최대 포지션 도달 — 매수 중단")
                break

            # 실제 현금 잔액 조회, 실패 시 고정 배분 fallback
            cash = await self.executor.get_cash_balance()
            if cash <= 0:
                cash = config.MAIN_CAPITAL
                logger.warning(f"[EMA] 잔액 조회 실패 — fallback {cash:,}원")

            slots = config.MAIN_MAX_POSITIONS - current_pos
            budget = cash // max(1, slots)

            symbol = c["symbol"]
            qty = budget // c["close"]
            if qty <= 0:
                logger.warning(f"[EMA] {symbol} 매수 수량 0 (주가 {c['close']:,}원, 예산 {budget:,}원)")
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
                logger.info(f"[EMA] 매수 완료: {symbol} {qty}주 @ {c['close']:,}원 (예산 {budget:,}원)")

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

            # Chandelier Exit 계산: 최고가 - ATR(14) × 3
            chandelier_stop = self._calc_chandelier_stop(symbol, pos["high_price"])

            logger.info(f"[EMA] {symbol} 현재가 {current_price:,} | "
                        f"수익률 {pnl_pct:+.1f}% | 최고가 {pos['high_price']:,} | "
                        f"Chandelier {chandelier_stop:,.0f} | 보유 {self._hold_days(pos)}일")

            reason = None

            # 1. 고정 손절 -6%
            if pnl_pct <= config.MAIN_STOP_LOSS_PCT:
                reason = f"고정 손절 ({pnl_pct:.1f}%)"

            # 2. Chandelier Exit: 현재가 < 최고가 - ATR×3
            elif chandelier_stop > 0 and current_price < chandelier_stop:
                ch_pct = (current_price - pos["high_price"]) / pos["high_price"] * 100
                reason = f"Chandelier Exit (최고가 {pos['high_price']:,} - ATR×3, 현재 {current_price:,}, {ch_pct:+.1f}%)"

            if reason:
                await self._sell_position(pos, reason, pnl_pct)

        if dirty:
            self.risk_manager._save()

    def _calc_chandelier_stop(self, symbol: str, high_price: float) -> float:
        """Chandelier Exit 스톱 가격 계산: 최고가 - ATR(14) × 배수"""
        today = datetime.now().strftime("%Y%m%d")
        cache_key = f"{symbol}_{today}"
        if cache_key not in self._atr_cache:
            data = get_daily_ohlcv(symbol, days=50)
            if not data or len(data["closes"]) < 15:
                return 0.0
            atr_values = calc_atr(data["highs"], data["lows"], data["closes"], 14)
            self._atr_cache[cache_key] = atr_values[-1]
        current_atr = self._atr_cache[cache_key]
        if current_atr <= 0:
            return 0.0
        return high_price - current_atr * config.MAIN_ATR_MULT

    async def check_dead_cross_exit(self):
        """장 마감 후 데드크로스 체크 (15:35 1회 호출)"""
        from strategy.screener import check_ema_dead_cross

        positions = self.risk_manager.get_positions(strategy="ema")
        dirty = False
        for pos in positions:
            if check_ema_dead_cross(pos["symbol"]):
                current = await self.executor.get_current_price(pos["symbol"])
                pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100 if current > 0 else 0
                logger.info(f"[EMA] {pos['symbol']} 데드크로스 감지 — 다음날 매도 예정")
                pos["exit_signal"] = True
                dirty = True
        if dirty:
            self.risk_manager._save()

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
