"""
갭 페이딩(롱) 전략 — 갭다운 매수 → 당일 청산

3%+ 갭다운 종목을 시가에 매수, 장중 되돌림으로 수익, 종가 전 매도.
공매도 없음. KIS API buy_us() → sell_us()로 완결.

백테스트: 코인주 3124건 승률64% 건당+2.70% Sharpe 3.85
"""

import asyncio
import logging
from datetime import date
from strategy.us_gap_fade_screener import USGapFadeScreener
import config

logger = logging.getLogger(__name__)


class USGapFadeStrategy:

    def __init__(self, risk_manager, executor):
        self.risk_manager = risk_manager
        self.executor = executor

        self.max_positions = config.GAP_FADE_MAX_POSITIONS
        self.stop_loss_pct = config.GAP_FADE_STOP_LOSS
        cap = config.GAP_FADE_CAPITAL_PER_POS
        if cap <= 0:
            cap = config.TOTAL_CAPITAL / self.max_positions
        self.capital_per_pos = cap / config.EXCHANGE_RATE_USD_KRW

        self.screener = USGapFadeScreener(
            groups=config.GAP_FADE_GROUPS,
            min_gap_pct=config.GAP_FADE_MIN_GAP,
        )

        self.positions = {}     # {sym: {entry_price, qty, stop_price, gap_pct, exchange}}
        self.prev_close = {}
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.today = None

    # ─────────────────────────────────────────
    # 1. 전일 종가 캐시 (22:00)
    # ─────────────────────────────────────────
    async def cache_prev_close(self):
        logger.info("=" * 50)
        logger.info("[갭페이드] 전일 종가 캐시 (KIS API)")

        if self.today != date.today():
            self.today = date.today()
            self.daily_pnl = 0.0
            self.daily_trades = 0

        self.prev_close = {}
        for sym in self.screener.symbols:
            exchange = self.screener.get_exchange(sym)
            try:
                info = await self.executor.get_us_price_info(sym, exchange)
                if info and info.get('base', 0) > 0:
                    self.prev_close[sym] = info['base']
            except Exception as e:
                logger.debug(f"  {sym} 전일종가 조회 실패: {e}")
            await asyncio.sleep(0.1)  # API 속도 제한 방지

        logger.info(f"전일 종가 {len(self.prev_close)}개 캐시 완료")

    # ─────────────────────────────────────────
    # 2. 갭다운 매수 (22:30)
    # ─────────────────────────────────────────
    async def execute_entry(self):
        logger.info("[갭페이드] 매수 실행")

        if not self.prev_close:
            await self.cache_prev_close()
            if not self.prev_close:
                return

        candidates = await self.screener.scan(self.executor, self.prev_close)
        if not candidates:
            logger.info("갭다운 종목 없음 — 오늘은 쉼")
            return

        slots = self.max_positions - len(self.positions)
        if slots <= 0:
            logger.info("포지션 풀")
            return

        entered = 0
        for cand in candidates:
            if entered >= slots:
                break

            sym = cand['symbol']
            if sym in self.positions:
                continue

            price = cand['current_price']
            gap = cand['gap_pct']
            exchange = self.screener.get_exchange(sym)

            qty = int(self.capital_per_pos / price)
            if qty <= 0:
                continue

            try:
                result = await self.executor.buy_us(
                    sym, qty, round(price, 2), exchange
                )
                if result and result.get('rt_cd') == '0':
                    stop = round(price * (1 - self.stop_loss_pct), 2)
                    self.positions[sym] = {
                        'entry_price': price,
                        'qty': qty,
                        'stop_price': stop,
                        'gap_pct': gap,
                        'exchange': exchange,
                    }
                    entered += 1
                    self.daily_trades += 1
                    logger.info(
                        f"  [매수] {sym} {qty}주 @${price:.2f} "
                        f"(갭{gap*100:+.1f}% 손절${stop:.2f})"
                    )
                else:
                    msg = result.get('msg1', '?') if result else '응답없음'
                    logger.warning(f"  [매수실패] {sym}: {msg}")
            except Exception as e:
                logger.error(f"  [매수에러] {sym}: {e}")

        logger.info(f"매수 {entered}건 (총 {len(self.positions)}포지션)")

    # ─────────────────────────────────────────
    # 3. 장중 모니터링 (15분마다)
    # ─────────────────────────────────────────
    async def check_exit(self):
        if not self.positions:
            return

        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            try:
                cur = await self.executor.get_us_current_price(
                    sym, pos['exchange']
                )
                if not cur or cur <= 0:
                    continue
            except Exception:
                continue

            entry = pos['entry_price']
            pnl_pct = cur / entry - 1

            # 손절: 진입가 대비 -5% 이하
            if cur <= pos['stop_price']:
                logger.warning(f"  [손절] {sym} ${entry:.2f}→${cur:.2f} ({pnl_pct*100:+.1f}%)")
                await self._sell(sym, cur, '손절')
            else:
                logger.info(f"  [모니터] {sym} ${entry:.2f}→${cur:.2f} ({pnl_pct*100:+.1f}%)")

    # ─────────────────────────────────────────
    # 4. 전량 매도 (04:50)
    # ─────────────────────────────────────────
    async def close_all(self):
        if not self.positions:
            logger.info("[갭페이드] 매도할 포지션 없음")
            self._report()
            return

        logger.info(f"[갭페이드] 전량 매도: {len(self.positions)}개")
        for sym in list(self.positions.keys()):
            try:
                cur = await self.executor.get_us_current_price(
                    sym, self.positions[sym]['exchange']
                )
                await self._sell(sym, cur if cur and cur > 0 else 0, '장마감')
            except Exception as e:
                logger.error(f"  [매도에러] {sym}: {e}")

        self._report()

    # ─────────────────────────────────────────
    # 내부
    # ─────────────────────────────────────────
    async def _sell(self, symbol, current_price, reason=''):
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        qty = pos['qty']
        entry = pos['entry_price']

        try:
            result = await self.executor.sell_us(
                symbol, qty,
                round(current_price, 2) if current_price > 0 else 0,
                pos['exchange']
            )
            if not result or result.get('rt_cd') != '0':
                msg = result.get('msg1', '?') if result else '응답없음'
                logger.error(f"  [매도실패] {symbol}: {msg}")
                return  # 매도 실패 시 포지션 유지
        except Exception as e:
            logger.error(f"  [매도실패] {symbol}: {e}")
            return  # 매도 실패 시 포지션 유지

        if current_price > 0:
            pnl_pct = current_price / entry - 1
            pnl_usd = (current_price - entry) * qty
        else:
            pnl_pct = 0
            pnl_usd = 0

        self.daily_pnl += pnl_usd
        self.daily_trades += 1

        logger.info(
            f"  [매도:{reason}] {symbol} {qty}주 "
            f"${entry:.2f}→${current_price:.2f} ({pnl_pct*100:+.1f}% ${pnl_usd:+.2f})"
        )
        del self.positions[symbol]

    def _report(self):
        krw = self.daily_pnl * config.EXCHANGE_RATE_USD_KRW
        logger.info("=" * 50)
        logger.info(f"[갭페이드] 일일 리포트")
        logger.info(f"  거래: {self.daily_trades}건")
        logger.info(f"  PnL: ${self.daily_pnl:+.2f} ({krw:+,.0f}원)")
        logger.info(f"  잔여: {len(self.positions)}포지션")
        logger.info("=" * 50)

    def get_status(self) -> dict:
        return {
            'strategy': 'gap_fade_long',
            'positions': {s: {
                'entry': p['entry_price'], 'qty': p['qty'],
                'stop': p['stop_price'], 'gap': p['gap_pct'],
            } for s, p in self.positions.items()},
            'count': len(self.positions),
            'daily_pnl_usd': round(self.daily_pnl, 2),
            'daily_trades': self.daily_trades,
        }
