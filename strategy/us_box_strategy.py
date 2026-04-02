"""미국 박스권 매매 전략

진입: Buy Zone(하단 25%) + 반등 확인 시그널 2/3 → 분할매수 3단계
청산: 저항선 -3% 익절 / ATR 기반 손절 (소프트+하드) / 종가 2일 확인
"""

import logging
from datetime import datetime
from collector.us_market_data import get_us_daily_ohlcv, bulk_download
from collector.us_universe import load_universe, fetch_us_universe, save_universe, is_universe_stale
from strategy.us_box_screener import scan_box_candidates
from strategy.indicators import atr, box_position_pct
import config

logger = logging.getLogger(__name__)


class USBoxStrategy:
    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor
        self._candidates = []  # 최근 스캔 결과
        self._pending_orders = {}  # {symbol: {order_no, ...}} 미체결 주문 추적

    async def daily_scan(self) -> list[dict]:
        """매일 1회: 유니버스 → 데이터 수집 → 박스권 스캔 → Buy Zone 후보

        미국장 시작 전 실행 (한국시간 22:00 또는 23:00)
        """
        logger.info("=== [US Box] 일일 스캔 시작 ===")

        # 1. 유니버스 로드 (주 1회 갱신)
        if is_universe_stale():
            logger.info("[US Box] 유니버스 갱신 중...")
            symbols = await fetch_us_universe()
            save_universe(symbols)
        universe = load_universe()
        if not universe:
            logger.error("[US Box] 유니버스 비어있음")
            return []

        # 2. 기본 필터 (시총/가격 정보가 없으므로 유니버스 전체 사용)
        # 실제 필터링은 스크리너에서 가격/거래량 기준으로 수행
        target_symbols = [u["symbol"] for u in universe]
        logger.info(f"[US Box] 유니버스: {len(target_symbols)}종목")

        # 3. 일봉 데이터 수집 (yfinance bulk)
        daily_data = bulk_download(target_symbols, days=config.US_BOX_LOOKBACK_DAYS + 30)
        logger.info(f"[US Box] 데이터 수집 완료: {len(daily_data)}종목")

        # 4. 박스권 스캔
        self._candidates = scan_box_candidates(universe, daily_data)
        logger.info(f"=== [US Box] 스캔 결과: {len(self._candidates)}종목 ===")

        return self._candidates

    async def execute_entry(self):
        """매수 실행 — 분할매수 1단계 (Buy Zone 진입 시)

        스캔 결과에서 리스크 체크 통과한 종목에 지정가 주문
        """
        if not self._candidates:
            logger.info("[US Box] 매수 후보 없음")
            return

        if not self.risk_manager.can_open_us_box_position():
            logger.info("[US Box] 신규 진입 불가 (리스크 한도 또는 최대 포지션)")
            return

        available_slots = config.US_BOX_MAX_POSITIONS - self._us_box_position_count()
        if available_slots <= 0:
            logger.info("[US Box] 최대 포지션 도달")
            return

        # 이미 보유중인 종목 제외
        held_symbols = {p["symbol"] for p in self.risk_manager.get_positions(strategy="us_box")}
        candidates = [c for c in self._candidates if c["symbol"] not in held_symbols]

        # 쿨다운 중인 종목 제외
        candidates = [c for c in candidates if not self.risk_manager.is_in_cooldown(c["symbol"])]

        budget_per_position = config.US_BOX_CAPITAL // config.US_BOX_MAX_POSITIONS

        for c in candidates[:available_slots]:
            symbol = c["symbol"]
            exchange = c["exchange"]
            support = c["support"]
            current = c["close"]
            atr_val = c["atr"]

            # 분할매수 1단계: 전체 예산의 1/3, 현재가 근처 지정가
            split_budget = int(budget_per_position * config.US_BOX_SPLIT_RATIO[0])
            qty = int(split_budget / current) if current > 0 else 0
            if qty <= 0:
                logger.warning(f"[US Box] {symbol} 매수 수량 0 (가격 ${current:.2f})")
                continue

            # 지정가: 현재가 (Buy Zone에 이미 있으므로)
            buy_price = round(current, 2)

            result = await self.executor.buy_us(symbol, qty, buy_price, exchange)
            if result:
                rt_cd = result.get("rt_cd", "")
                if rt_cd == "0":
                    order_no = result.get("output", {}).get("ODNO", "")
                    self.risk_manager.add_position(
                        symbol=symbol,
                        qty=qty,
                        entry_price=buy_price,
                        strategy="us_box",
                        entry_date=datetime.now().strftime("%Y%m%d"),
                        support=support,
                        resistance=c["resistance"],
                        atr=atr_val,
                        exchange=exchange,
                        split_stage=1,
                        soft_stop_days=0,
                    )

                    logger.info(
                        f"[US Box] 매수 1/3: {symbol} {qty}주 @ ${buy_price:.2f} "
                        f"(지지=${support:.2f}, 저항=${c['resistance']:.2f})"
                    )
                else:
                    logger.error(f"[US Box] 매수 실패 {symbol}: {result.get('msg1', '')}")

    async def check_exit(self):
        """청산 조건 확인 (30분마다)

        1. 하드 스톱: 장중 지지선 - 2.0×ATR → 즉시 손절
        2. 소프트 스톱: 종가 기준 지지선 - 1.5×ATR, 2일 연속 → 손절
        3. 익절: 저항선 -3% 도달 → 매도
        4. 박스 이탈 감지: ADX > 25 → 전략 일시 중단 (추후)
        """
        positions = self.risk_manager.get_positions(strategy="us_box")
        if not positions:
            return

        for pos in list(positions):
            symbol = pos["symbol"]
            exchange = pos.get("exchange", "NAS")
            entry_price = pos["entry_price"]
            support = pos.get("support", 0)
            resistance = pos.get("resistance", 0)
            atr_val = pos.get("atr", 0)

            if support <= 0 or resistance <= 0 or atr_val <= 0:
                continue

            # 현재가 조회
            current = await self.executor.get_us_current_price(symbol, exchange)
            if current <= 0:
                continue

            pnl_pct = (current - entry_price) / entry_price * 100
            reason = None

            # 1. 하드 스톱: 지지선 - 2.0 × ATR (무조건 손절)
            hard_stop = support - config.US_BOX_HARD_STOP_ATR * atr_val
            if current <= hard_stop:
                reason = f"하드 스톱 (${current:.2f} <= ${hard_stop:.2f})"

            # 2. 소프트 스톱: 지지선 - 1.5 × ATR, 종가 2일 연속 확인
            elif not reason:
                soft_stop = support - config.US_BOX_SOFT_STOP_ATR * atr_val
                if current <= soft_stop:
                    pos["soft_stop_days"] = pos.get("soft_stop_days", 0) + 1
                    if pos["soft_stop_days"] >= 2:
                        reason = f"소프트 스톱 2일 연속 (${current:.2f} <= ${soft_stop:.2f})"
                    else:
                        logger.info(f"[US Box] {symbol} 소프트 스톱 1일차 (${current:.2f})")
                else:
                    pos["soft_stop_days"] = 0  # 복귀하면 리셋

            # 3. 익절: 저항선 - 3% 도달
            take_profit_price = resistance * (1 - config.US_BOX_TAKE_PROFIT_PCT / 100)
            if not reason and current >= take_profit_price:
                reason = f"익절 (${current:.2f} >= ${take_profit_price:.2f})"

            if reason:
                await self._sell_position(pos, reason, pnl_pct, current)

        self.risk_manager._save()

    async def check_split_entry(self):
        """분할매수 2~3단계 확인

        2단계: 지지선 +3~5%에서 반등 확인 시 추가 1/3
        3단계: 지지선 터치 후 양봉 확인 시 마지막 1/3
        """
        positions = self.risk_manager.get_positions(strategy="us_box")

        for pos in positions:
            split_stage = pos.get("split_stage", 1)
            if split_stage >= 3:
                continue  # 이미 풀 포지션

            symbol = pos["symbol"]
            exchange = pos.get("exchange", "NAS")
            support = pos.get("support", 0)
            resistance = pos.get("resistance", 0)

            if support <= 0:
                continue

            current = await self.executor.get_us_current_price(symbol, exchange)
            if current <= 0:
                continue

            bp = box_position_pct(current, support, resistance) if resistance > support else 50

            should_add = False

            if split_stage == 1 and bp <= 15:
                # 2단계: 지지선 +15% 이내까지 내려왔으면 추가
                should_add = True
            elif split_stage == 2 and bp <= 5:
                # 3단계: 지지선 터치 수준
                should_add = True

            if should_add:
                budget_per_position = config.US_BOX_CAPITAL // config.US_BOX_MAX_POSITIONS
                ratio = config.US_BOX_SPLIT_RATIO[split_stage]
                split_budget = int(budget_per_position * ratio)
                qty = int(split_budget / current) if current > 0 else 0

                if qty <= 0:
                    continue

                result = await self.executor.buy_us(symbol, qty, round(current, 2), exchange)
                if result and result.get("rt_cd") == "0":
                    # 평균 매입가 업데이트
                    old_qty = pos["qty"]
                    old_cost = pos["entry_price"] * old_qty
                    new_cost = current * qty
                    total_qty = old_qty + qty
                    pos["qty"] = total_qty
                    pos["entry_price"] = round((old_cost + new_cost) / total_qty, 2)
                    pos["split_stage"] = split_stage + 1
                    self.risk_manager._save()

                    logger.info(
                        f"[US Box] 분할매수 {split_stage + 1}/3: {symbol} +{qty}주 @ ${current:.2f} "
                        f"(평단 ${pos['entry_price']:.2f}, 총 {total_qty}주)"
                    )

    async def cancel_stale_orders(self):
        """미체결 주문 정리 (장 마감 전)"""
        for symbol, info in list(self._pending_orders.items()):
            order_no = info.get("order_no", "")
            exchange = info.get("exchange", "NAS")
            if order_no:
                await self.executor.kis.cancel_us_order(order_no, symbol, exchange)
                logger.info(f"[US Box] 미체결 취소: {symbol} #{order_no}")
        self._pending_orders.clear()

    async def _sell_position(self, pos: dict, reason: str, pnl_pct: float, current_price: float):
        """포지션 매도"""
        symbol = pos["symbol"]
        qty = pos["qty"]
        exchange = pos.get("exchange", "NAS")

        result = await self.executor.sell_us(symbol, qty, round(current_price, 2), exchange)
        if result and result.get("rt_cd") == "0":
            # 달러 기준 PnL
            pnl_usd = round((current_price - pos["entry_price"]) * qty, 2)
            # 원화 환산 (대략 1350원/달러)
            pnl_krw = int(pnl_usd * config.EXCHANGE_RATE_USD_KRW)
            self.risk_manager.close_position(symbol, pnl_krw, reason, strategy="us_box")
            logger.info(
                f"[US Box] 매도: {symbol} {qty}주 @ ${current_price:.2f} | {reason} "
                f"| 손익 ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)"
            )
        else:
            logger.error(f"[US Box] 매도 실패 {symbol}: {result}")

    def _get_position(self, symbol: str):
        for p in self.risk_manager.positions:
            if p["symbol"] == symbol and p["strategy"] == "us_box":
                return p
        return None

    def _us_box_position_count(self) -> int:
        return len(self.risk_manager.get_positions(strategy="us_box"))
