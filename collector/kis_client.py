"""한국투자증권 OpenAPI REST 클라이언트"""

import time
import logging
import httpx
import config

logger = logging.getLogger(__name__)

BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"

TR_ID = {
    "buy": {"real": "TTTC0802U", "paper": "VTTC0802U"},
    "sell": {"real": "TTTC0801U", "paper": "VTTC0801U"},
    "balance": {"real": "TTTC8434R", "paper": "VTTC8434R"},
    "orders": {"real": "TTTC8001R", "paper": "VTTC8001R"},
    "cancel": {"real": "TTTC0803U", "paper": "VTTC0803U"},
    "price": "FHKST01010100",
    "askprice": "FHKST01010200",
    "daily_chart": "FHKST03010100",
    "minute_chart": "FHKST03010200",
    "volume_rank": "FHPST01710000",
    "investor": "FHKST01010900",
    "ccnl": "FHKST01010300",
    # ─── 해외주식 ───
    "us_price": "HHDFS00000300",
    "us_daily_chart": "HHDFS76240000",
    "us_buy": {"real": "JTTT1002U", "paper": "VTTT1002U"},
    "us_sell": {"real": "JTTT1006U", "paper": "VTTT1006U"},
    "us_balance": {"real": "JTTT3012R", "paper": "VTTT3012R"},
    "us_order_status": {"real": "JTTT3018R", "paper": "VTTT3018R"},
    "us_cancel": {"real": "JTTT1004U", "paper": "VTTT1004U"},
}


class KISClient:
    def __init__(self):
        self.app_key = config.KIS_APP_KEY
        self.app_secret = config.KIS_APP_SECRET
        self.account_no = config.KIS_ACCOUNT_NO
        self.is_paper = config.KIS_IS_PAPER

        self.base_url = BASE_URL_PAPER if self.is_paper else BASE_URL_REAL
        self.access_token = ""
        self.token_expired_at = 0
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10)

    async def close(self):
        await self.client.aclose()

    def _tr_id(self, name: str) -> str:
        tr = TR_ID[name]
        if isinstance(tr, dict):
            return tr["paper"] if self.is_paper else tr["real"]
        return tr

    def _ord_dvsn(self, price: int) -> str:
        return "00" if price > 0 else "01"  # 지정가=00, 시장가=01 (모의/실전 동일)

    def _acnt_prefix(self) -> str:
        return self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]

    def _acnt_suffix(self) -> str:
        return self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

    async def _ensure_token(self):
        if self.access_token and time.time() < self.token_expired_at - 60:
            return
        resp = await self.client.post("/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        })
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.token_expired_at = time.time() + data.get("expires_in", 86400)
        logger.info("KIS 토큰 발급 완료")

    async def _headers(self, tr_id: str) -> dict:
        await self._ensure_token()
        return {
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    # ─── 시세 조회 ───

    async def get_price(self, symbol: str) -> dict:
        """현재가 조회"""
        headers = await self._headers(self._tr_id("price"))
        resp = await self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_daily_chart(self, symbol: str, start_date: str, end_date: str) -> dict:
        """일봉 차트 (YYYYMMDD)"""
        headers = await self._headers(self._tr_id("daily_chart"))
        resp = await self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers,
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_minute_chart(self, symbol: str, time_unit: str = "1") -> dict:
        """당일 분봉"""
        headers = await self._headers(self._tr_id("minute_chart"))
        resp = await self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=headers,
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_ETC_CLS_CODE": time_unit,
                "FID_INPUT_HOUR_1": "160000",
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_conclusion(self, symbol: str) -> dict:
        """체결 데이터 (체결강도 포함)"""
        headers = await self._headers(self._tr_id("ccnl"))
        resp = await self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-ccnl",
            headers=headers,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        resp.raise_for_status()
        return resp.json()

    # ─── 주문 ───

    async def buy(self, symbol: str, qty: int, price: int = 0) -> dict:
        """매수 (price=0 시장가)"""
        headers = await self._headers(self._tr_id("buy"))
        resp = await self.client.post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers,
            json={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "PDNO": symbol,
                "ORD_DVSN": self._ord_dvsn(price),
                "ORD_QTY": str(qty),
                "ORD_UNPR": str(price),
            },
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"매수 주문: {symbol} {qty}주 {'시장가' if price == 0 else f'{price}원'} -> {result.get('msg1', '')}")
        return result

    async def sell(self, symbol: str, qty: int, price: int = 0) -> dict:
        """매도 (price=0 시장가)"""
        headers = await self._headers(self._tr_id("sell"))
        resp = await self.client.post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers,
            json={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "PDNO": symbol,
                "ORD_DVSN": self._ord_dvsn(price),
                "ORD_QTY": str(qty),
                "ORD_UNPR": str(price),
            },
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"매도 주문: {symbol} {qty}주 -> {result.get('msg1', '')}")
        return result

    # ─── 계좌 ───

    async def get_balance(self) -> dict:
        """잔고 조회"""
        headers = await self._headers(self._tr_id("balance"))
        resp = await self.client.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=headers,
            params={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_approval_key(self) -> str:
        """WebSocket용 Approval Key"""
        resp = await self.client.post("/oauth2/Approval", json={
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        })
        resp.raise_for_status()
        return resp.json()["approval_key"]

    # ─── 해외주식 시세 조회 ───

    async def get_us_price(self, symbol: str, exchange: str = "NAS") -> dict:
        """해외주식 현재가 조회
        exchange: NAS(나스닥), NYS(뉴욕), AMS(아멕스)
        """
        headers = await self._headers(self._tr_id("us_price"))
        resp = await self.client.get(
            "/uapi/overseas-price/v1/quotations/price",
            headers=headers,
            params={"AUTH": "", "EXCD": exchange, "SYMB": symbol},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_us_daily_chart(self, symbol: str, exchange: str = "NAS",
                                  end_date: str = "", period: str = "D") -> dict:
        """해외주식 기간별 시세 (일봉)
        period: D=일, W=주, M=월
        end_date: YYYYMMDD (빈 문자열이면 오늘)
        """
        headers = await self._headers(self._tr_id("us_daily_chart"))
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "GUBN": "0" if period == "D" else ("1" if period == "W" else "2"),
            "BYMD": end_date,
            "MODP": "1",  # 수정주가 반영
        }
        resp = await self.client.get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    # ─── 해외주식 주문 ───

    async def buy_us(self, symbol: str, qty: int, price: float,
                      exchange: str = "NAS") -> dict:
        """해외주식 지정가 매수
        exchange: NASD(나스닥), NYSE(뉴욕), AMEX(아멕스)
        """
        ovrs_excg = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}.get(exchange, "NASD")
        headers = await self._headers(self._tr_id("us_buy"))
        resp = await self.client.post(
            "/uapi/overseas-stock/v1/trading/order",
            headers=headers,
            json={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "OVRS_EXCG_CD": ovrs_excg,
                "PDNO": symbol,
                "ORD_DVSN": "00",  # 지정가
                "ORD_QTY": str(qty),
                "OVRS_ORD_UNPR": f"{price:.2f}",
            },
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"[US] 매수 주문: {symbol} {qty}주 @ ${price:.2f} -> {result.get('msg1', '')}")
        return result

    async def sell_us(self, symbol: str, qty: int, price: float,
                       exchange: str = "NAS") -> dict:
        """해외주식 지정가 매도"""
        ovrs_excg = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}.get(exchange, "NASD")
        headers = await self._headers(self._tr_id("us_sell"))
        resp = await self.client.post(
            "/uapi/overseas-stock/v1/trading/order",
            headers=headers,
            json={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "OVRS_EXCG_CD": ovrs_excg,
                "PDNO": symbol,
                "ORD_DVSN": "00",
                "ORD_QTY": str(qty),
                "OVRS_ORD_UNPR": f"{price:.2f}",
            },
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"[US] 매도 주문: {symbol} {qty}주 @ ${price:.2f} -> {result.get('msg1', '')}")
        return result

    async def get_us_balance(self) -> dict:
        """해외주식 잔고 조회"""
        headers = await self._headers(self._tr_id("us_balance"))
        resp = await self.client.get(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=headers,
            params={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "OVRS_EXCG_CD": "NASD",
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_us_order(self, order_no: str, symbol: str,
                               exchange: str = "NAS") -> dict:
        """해외주식 주문 취소"""
        ovrs_excg = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}.get(exchange, "NASD")
        headers = await self._headers(self._tr_id("us_cancel"))
        resp = await self.client.post(
            "/uapi/overseas-stock/v1/trading/order-rvsecncl",
            headers=headers,
            json={
                "CANO": self._acnt_prefix(),
                "ACNT_PRDT_CD": self._acnt_suffix(),
                "OVRS_EXCG_CD": ovrs_excg,
                "PDNO": symbol,
                "ORGN_ODNO": order_no,
                "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
                "ORD_QTY": "0",  # 전량
                "OVRS_ORD_UNPR": "0",
            },
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"[US] 주문 취소: {order_no} -> {result.get('msg1', '')}")
        return result
