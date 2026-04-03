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

# ─── 주전략: 상대강도 로테이션 ───
# 워크포워드 검증: 평균 +40.6%/3개월, 양수구간 85%, PF 1.79
RS_LOOKBACK = 10              # 수익률 계산 기간 (거래일)
RS_TOP_N = 3                  # 상위 N종목 보유
RS_REBAL_DAYS = 5             # 리밸런싱 주기 (거래일)
RS_STOP_LOSS_PCT = -5.0       # 개별 종목 손절
MAIN_MAX_POSITIONS = 3        # 동시 보유 최대 3종목

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

# ─── 미국 박스권 전략 ───
US_BOX_CAPITAL = 300_000              # 30만원 (환율 적용 시 ~$220)
US_BOX_MAX_POSITIONS = 3              # 최대 동시 보유 종목
US_BOX_LOOKBACK_DAYS = 60             # 박스권 판별 기간 (거래일)
US_BOX_MIN_TOUCHES = 2                # 지지/저항 최소 터치 횟수
US_BOX_TOUCH_TOLERANCE = 0.015        # 터치 허용오차 ±1.5%
US_BOX_MIN_WIDTH_PCT = 5.0            # 최소 박스폭 %
US_BOX_MAX_WIDTH_PCT = 15.0           # 최대 박스폭 %
US_BOX_BUY_ZONE_PCT = 25.0            # Buy Zone (하단 25%)
US_BOX_ADX_MAX = 20                   # 횡보 판단 ADX 상한
US_BOX_SOFT_STOP_ATR = 2.5            # 소프트 손절 ATR 배수 (백테스트 최적)
US_BOX_HARD_STOP_ATR = 3.5            # 하드 손절 ATR 배수 (백테스트 최적)
US_BOX_TAKE_PROFIT_PCT = 3.0          # 저항선 -3%에서 익절
US_BOX_SIGNAL_MIN = 2                 # 최소 반등 확인 시그널 (3개 중)
US_BOX_SPLIT_RATIO = [0.34, 0.33, 0.33]  # 분할매수 비율

# ─── 미국 유니버스 필터 ───
US_MIN_MARKET_CAP = 300_000_000       # 시총 $300M+
US_MIN_AVG_VOLUME = 100_000           # 일평균 거래량 10만주+ (백테스트 조정)
US_MIN_PRICE = 5.0                    # 최소 주가 $5
# 코인 관련주 (백테스트 최적: 채굴주+거래소 12종목, +9.1%)
# ETF는 박스권 미형성, 레버리지 ETF는 변동성 과대로 제외
US_PRIORITY_SYMBOLS = [
    "COIN", "MARA", "RIOT", "MSTR", "ETHE", "BITO",
    "HOOD", "BITF", "CLSK", "HUT", "CORZ", "IREN",
]

# ─── 미국 리스크 한도 (원화 기준) ───
US_DAILY_MAX_LOSS = -50_000           # 일일 최대 손실 5만원
US_WEEKLY_MAX_LOSS = -150_000         # 주간 최대 손실 15만원
US_MONTHLY_MAX_LOSS = -300_000        # 월간 최대 손실 30만원
EXCHANGE_RATE_USD_KRW = 1350          # 원/달러 환율 (실전: 실시간 조회로 교체)

# ─── 데이터 ───
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
