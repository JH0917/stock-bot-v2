"""갭 페이딩(롱) 스크리너 — 3%+ 갭다운 종목 감지"""

import logging

logger = logging.getLogger(__name__)


class USGapFadeScreener:

    COIN = ['MARA','RIOT','CLSK','HUT','COIN','MSTR','BITF','IREN','ETHE','BITO','HOOD']
    LEVERAGE = ['TQQQ','SOXL','FNGU','LABU','TECL','SQQQ','TZA']
    VOLATILE = ['TSLA','NVDA','AMD','PLTR','SOFI','DKNG','RBLX','ROKU','SNAP']

    EXCHANGE_MAP = {
        'MARA': 'NAS', 'RIOT': 'NAS', 'CLSK': 'NAS', 'HUT': 'NYS',
        'COIN': 'NAS', 'MSTR': 'NAS', 'BITF': 'NYS', 'IREN': 'NAS',
        'ETHE': 'NYS', 'BITO': 'NYS', 'HOOD': 'NAS',
        'TQQQ': 'NAS', 'SOXL': 'NYS', 'FNGU': 'NYS', 'LABU': 'NYS',
        'TECL': 'NYS', 'SQQQ': 'NAS', 'TZA': 'NYS',
        'TSLA': 'NAS', 'NVDA': 'NAS', 'AMD': 'NAS', 'PLTR': 'NYS',
        'SOFI': 'NAS', 'DKNG': 'NAS', 'RBLX': 'NYS', 'ROKU': 'NAS',
        'SNAP': 'NYS',
    }

    def __init__(self, groups=None, min_gap_pct=0.03):
        self.symbols = []
        for grp in (groups or ['coin', 'leverage', 'volatile']):
            if grp == 'coin': self.symbols.extend(self.COIN)
            elif grp == 'leverage': self.symbols.extend(self.LEVERAGE)
            elif grp == 'volatile': self.symbols.extend(self.VOLATILE)
        self.min_gap_pct = min_gap_pct

    def get_exchange(self, symbol):
        return self.EXCHANGE_MAP.get(symbol, 'NAS')

    async def scan(self, executor, prev_close: dict) -> list:
        """
        갭다운 종목 감지: 현재가가 전일종가 대비 -3% 이하

        Returns: [{'symbol', 'gap_pct', 'prev_close', 'current_price'}, ...]
                 갭 크기(절대값) 내림차순
        """
        candidates = []

        for sym in self.symbols:
            if sym not in prev_close:
                continue
            try:
                cur = await executor.get_us_current_price(sym, self.get_exchange(sym))
                if not cur or cur <= 0:
                    continue
            except Exception as e:
                logger.debug(f"{sym} 가격조회 실패: {e}")
                continue

            prev = prev_close[sym]
            if prev <= 0:
                continue

            gap = (cur - prev) / prev  # 음수 = 갭다운

            if gap <= -self.min_gap_pct:
                candidates.append({
                    'symbol': sym,
                    'gap_pct': gap,
                    'prev_close': prev,
                    'current_price': cur,
                })
                logger.info(f"  갭다운: {sym} {gap*100:+.1f}% (${prev:.2f}→${cur:.2f})")

        # 갭 크기(절대값) 큰 순 정렬
        candidates.sort(key=lambda x: x['gap_pct'])
        logger.info(f"갭다운 {self.min_gap_pct*100:.0f}%+ 종목: {len(candidates)}개")
        return candidates
