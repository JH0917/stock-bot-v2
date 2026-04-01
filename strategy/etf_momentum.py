"""보조전략: Intraday Momentum ETF

장 초반 30분 방향이 장 마감 30분을 예측 (Gao et al. 2018)
KODEX 200 / KODEX 코스닥150 대상, 거래세 면제
"""

import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)


class ETFMomentumStrategy:
    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor
        self.open_prices = {}   # {symbol: 09:00 시가}
        self.price_0930 = {}    # {symbol: 09:30 가격}
        self.entry_price = {}   # {symbol: 진입 가격}

    async def capture_open(self):
        """09:00 시가 저장"""
        for sym in config.ETF_SYMBOLS:
            price = await self.executor.get_current_price(sym)
            if price > 0:
                self.open_prices[sym] = price
                logger.info(f"[ETF] {sym} 시가 저장: {price:,}원")

    async def check_entry(self):
        """09:31 진입 판단"""
        if not self.risk_manager.can_open_sub_position():
            logger.info("[ETF] 진입 불가 (리스크 한도)")
            return

        for sym in config.ETF_SYMBOLS:
            if sym not in self.open_prices:
                continue
            if sym in self.entry_price:
                continue  # 이미 보유 중

            current = await self.executor.get_current_price(sym)
            if current <= 0:
                continue

            open_price = self.open_prices[sym]
            momentum = (current - open_price) / open_price * 100

            # 조건 1: 초반 30분 수익률 > +0.3% (롱만)
            if momentum < config.ETF_MOMENTUM_THRESHOLD:
                logger.info(f"[ETF] {sym} 모멘텀 {momentum:.2f}% — 기준 미달, 패스")
                continue

            # 조건 2: 거래량 비율 체크 (평소 대비 거래량 충분한지)
            volume_ratio = await self._get_volume_ratio(sym)
            if volume_ratio < config.ETF_VOLUME_RATIO:
                logger.info(f"[ETF] {sym} 거래량 비율 {volume_ratio:.2f} — 기준({config.ETF_VOLUME_RATIO}) 미달, 패스")
                continue

            # 매수
            budget = config.SUB_CAPITAL
            qty = budget // current
            if qty <= 0:
                continue

            result = await self.executor.buy(sym, qty)
            if result:
                self.entry_price[sym] = current
                self.risk_manager.add_position(
                    symbol=sym,
                    qty=qty,
                    entry_price=current,
                    strategy="etf",
                    entry_date=datetime.now().strftime("%Y%m%d"),
                )
                logger.info(f"[ETF] 매수: {sym} {qty}주 @ {current:,}원 (모멘텀 {momentum:.2f}%)")

    async def check_exit(self):
        """1분마다 익절/손절 확인"""
        positions = self.risk_manager.get_positions(strategy="etf")

        for pos in positions:
            symbol = pos["symbol"]
            entry_price = pos["entry_price"]

            current = await self.executor.get_current_price(symbol)
            if current <= 0:
                continue

            pnl_pct = (current - entry_price) / entry_price * 100
            reason = None

            # 익절 +0.5%
            if pnl_pct >= config.ETF_TAKE_PROFIT_PCT:
                reason = f"익절 ({pnl_pct:.2f}%)"

            # 손절 -0.3%
            elif pnl_pct <= config.ETF_STOP_LOSS_PCT:
                reason = f"손절 ({pnl_pct:.2f}%)"

            if reason:
                await self._sell(pos, reason, pnl_pct)

    async def close_all(self):
        """15:15 전량 청산"""
        positions = self.risk_manager.get_positions(strategy="etf")
        for pos in positions:
            current = await self.executor.get_current_price(pos["symbol"])
            if current <= 0:
                logger.warning(f"[ETF] {pos['symbol']} 현재가 조회 실패, 시장가 매도 시도")
            pnl_pct = (current - pos["entry_price"]) / pos["entry_price"] * 100 if current > 0 else 0
            await self._sell(pos, "시간 청산 (15:15)", pnl_pct)
        self.open_prices.clear()
        self.price_0930.clear()
        self.entry_price.clear()

    async def _get_volume_ratio(self, symbol: str) -> float:
        """현재 거래량 / (최근 5일 평균 거래량 * 시간비율) 비율
        장중에는 당일 거래량이 적으므로 경과 시간 비율로 보정
        """
        from collector.market_data import get_daily_ohlcv
        data = get_daily_ohlcv(symbol, days=10)
        if not data or len(data["volumes"]) < 6:
            return 0.0
        recent_avg = sum(data["volumes"][-6:-1]) / 5  # 최근 5일 평균 (오늘 제외)
        today_vol = data["volumes"][-1]
        # 장 시작 후 경과 시간 비율로 보정 (09:00~15:30 = 390분)
        now = datetime.now()
        minutes_elapsed = (now.hour - 9) * 60 + now.minute
        time_ratio = min(max(minutes_elapsed / 390, 0.01), 1.0)
        expected_vol = recent_avg * time_ratio
        return today_vol / expected_vol if expected_vol > 0 else 0.0

    async def _sell(self, pos: dict, reason: str, pnl_pct: float):
        symbol = pos["symbol"]
        qty = pos["qty"]
        result = await self.executor.sell(symbol, qty)
        if result:
            pnl = int(pos["entry_price"] * qty * pnl_pct / 100)
            self.risk_manager.close_position(symbol, pnl, reason, strategy="etf")
            self.entry_price.pop(symbol, None)
            logger.info(f"[ETF] 매도: {symbol} | {reason} | 손익 {pnl:+,}원")
