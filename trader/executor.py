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

    # ─── 해외주식 ───

    async def get_us_current_price(self, symbol: str, exchange: str = "NAS") -> float:
        """해외주식 현재가 조회, 실패 시 0.0"""
        try:
            data = await self.kis.get_us_price(symbol, exchange)
            output = data.get("output", {})
            price = float(output.get("last", 0) or output.get("stck_prpr", 0))
            return price
        except Exception as e:
            logger.error(f"[US] 현재가 조회 실패 {symbol}: {e}")
            return 0.0

    async def get_us_price_info(self, symbol: str, exchange: str = "NAS") -> dict:
        """해외주식 현재가 + 전일종가 조회
        Returns: {'last': float, 'base': float} 또는 실패 시 빈 dict
        """
        try:
            data = await self.kis.get_us_price(symbol, exchange)
            output = data.get("output", {})
            last = float(output.get("last", 0) or 0)
            base = float(output.get("base", 0) or 0)
            if last > 0 and base > 0:
                return {"last": last, "base": base}
            return {}
        except Exception as e:
            logger.error(f"[US] 시세 조회 실패 {symbol}: {e}")
            return {}

    async def buy_us(self, symbol: str, qty: int, price: float,
                      exchange: str = "NAS") -> dict:
        """해외주식 지정가 매수"""
        try:
            return await self.kis.buy_us(symbol, qty, price, exchange)
        except Exception as e:
            logger.error(f"[US] 매수 예외 {symbol}: {e}")
            return {"rt_cd": "-1", "msg1": str(e)}

    async def sell_us(self, symbol: str, qty: int, price: float,
                       exchange: str = "NAS") -> dict:
        """해외주식 지정가 매도"""
        try:
            return await self.kis.sell_us(symbol, qty, price, exchange)
        except Exception as e:
            logger.error(f"[US] 매도 예외 {symbol}: {e}")
            return {"rt_cd": "-1", "msg1": str(e)}

    async def get_us_positions(self) -> dict:
        """해외주식 실제 보유종목 조회 (NASD + NYSE)
        Returns: {symbol: {'qty': int, 'avg_price': float, 'exchange': str}, ...}
        """
        positions = {}
        for excg in ["NASD", "NYSE"]:
            try:
                data = await self.kis.get_us_balance(excg)
                for item in data.get("output1", []):
                    sym = item.get("ovrs_pdno", "")
                    qty = int(item.get("ovrs_cblc_qty", 0))
                    avg_price = float(item.get("pchs_avg_pric", 0))
                    if sym and qty > 0:
                        positions[sym] = {
                            'qty': qty,
                            'avg_price': avg_price,
                            'exchange': excg,
                        }
            except Exception as e:
                logger.error(f"[US] 잔고 조회 실패 {excg}: {e}")
        return positions
