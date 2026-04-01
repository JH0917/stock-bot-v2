"""주문 실행기 — KIS API를 통한 매수/매도"""

import logging
from collector.kis_client import KISClient

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self):
        self.kis = KISClient()

    async def close(self):
        await self.kis.close()

    async def get_current_price(self, symbol: str) -> int:
        """현재가 조회, 실패 시 0"""
        try:
            data = await self.kis.get_price(symbol)
            output = data.get("output", {})
            price = int(output.get("stck_prpr", 0))
            return price
        except Exception as e:
            logger.error(f"현재가 조회 실패 {symbol}: {e}")
            return 0

    async def buy(self, symbol: str, qty: int) -> bool:
        """시장가 매수"""
        try:
            result = await self.kis.buy(symbol, qty, price=0)
            rt_cd = result.get("rt_cd", "")
            if rt_cd == "0":
                return True
            logger.error(f"매수 실패 {symbol}: {result.get('msg1', '')}")
            return False
        except Exception as e:
            logger.error(f"매수 예외 {symbol}: {e}")
            return False

    async def sell(self, symbol: str, qty: int) -> bool:
        """시장가 매도"""
        try:
            result = await self.kis.sell(symbol, qty, price=0)
            rt_cd = result.get("rt_cd", "")
            if rt_cd == "0":
                return True
            logger.error(f"매도 실패 {symbol}: {result.get('msg1', '')}")
            return False
        except Exception as e:
            logger.error(f"매도 예외 {symbol}: {e}")
            return False
