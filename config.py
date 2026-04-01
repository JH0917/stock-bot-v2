"""전역 설정"""

import os

# ─── KIS API ───
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
KIS_IS_PAPER = os.getenv("KIS_IS_PAPER", "true").lower() == "true"

# ─── 자금 배분 ───
TOTAL_CAPITAL = 1_000_000  # 총 자본 100만원
MAIN_CAPITAL = 700_000     # 주전략 70만원
SUB_CAPITAL = 300_000      # 보조전략 30만원

# ─── 주전략: RSI(2) + Holy Grail ───
RSI_PERIOD = 2
RSI_ENTRY_THRESHOLD = 5       # RSI(2) < 5 진입
RSI_EXIT_THRESHOLD = 70       # RSI(2) > 70 청산
ADX_PERIOD = 14
ADX_MIN = 30                  # ADX > 30 추세 필터
MA_LONG = 200                 # 200일 이동평균
MAIN_STOP_LOSS_PCT = -3.0     # 고정 손절 -3%
MAIN_TRAILING_STOP_PCT = -2.5 # 추적 손절 -2.5%
MAIN_MAX_HOLD_DAYS = 5        # 최대 보유 5거래일
MAIN_MAX_POSITIONS = 2        # 동시 보유 최대 2종목
MAIN_COOLDOWN_DAYS = 3        # 손절 후 동일 종목 재진입 금지 기간

# ─── 보조전략: ETF Intraday Momentum ───
ETF_SYMBOLS = ["069500", "229200"]  # KODEX 200, KODEX 코스닥150
ETF_MOMENTUM_THRESHOLD = 0.3  # 장 초반 30분 수익률 0.3% 이상
ETF_TAKE_PROFIT_PCT = 0.5     # 익절 +0.5%
ETF_STOP_LOSS_PCT = -0.3      # 손절 -0.3%
ETF_VOLUME_RATIO = 1.2        # 거래량 비율 기준

# ─── 리스크 관리 ───
DAILY_MAX_LOSS = -30_000      # 일일 최대 손실 3만원
WEEKLY_MAX_LOSS = -100_000    # 주간 최대 손실 10만원
MONTHLY_MAX_LOSS = -200_000   # 월간 최대 손실 20만원
MAX_DAILY_TRADES = 5          # 일일 최대 매매 횟수

# ─── 스크리닝 ───
MIN_TRADE_VALUE = 5_000_000_000   # 최소 거래대금 50억
MIN_PRICE = 5_000                  # 최소 주가 5,000원
SCREEN_TOP_N = 20                  # 스크리닝 상위 N개

# ─── 데이터 ───
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
