"""전역 설정 — 갭 페이딩(롱) 전략 전용"""

import os

# ─── KIS API ───
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
KIS_IS_PAPER = os.getenv("KIS_IS_PAPER", "true").lower() == "true"

# ─── 자금 ───
TOTAL_CAPITAL = 1_000_000         # 총 자본 100만원
EXCHANGE_RATE_USD_KRW = 1350      # 원/달러 환율

# ─── 갭 페이딩(롱) 전략 ───
# 3%+ 갭다운 종목 시가 매수 → 종가 매도
# 백테스트: 코인주 3124건 승률64% 건당+2.70% Sharpe 3.85
GAP_FADE_MAX_POSITIONS = 5        # 동시 최대 포지션
GAP_FADE_MIN_GAP = 0.03           # 최소 갭다운 3%
GAP_FADE_STOP_LOSS = 0.05         # 장중 손절 -5%
GAP_FADE_CAPITAL_PER_POS = 0      # 0이면 자동 (총자본/최대포지션)

# 모니터링 종목 그룹
GAP_FADE_GROUPS = ['coin', 'leverage', 'volatile']

# ─── 리스크 관리 ───
DAILY_MAX_LOSS = -50_000          # 일일 최대 손실 5만원
WEEKLY_MAX_LOSS = -150_000        # 주간 최대 손실 15만원
MONTHLY_MAX_LOSS = -300_000       # 월간 최대 손실 30만원
MAX_DAILY_TRADES = 20             # 일일 최대 매매

# ─── 데이터 ───
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
