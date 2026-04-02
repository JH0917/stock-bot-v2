"""기술 지표 계산 — RSI(2), ADX(14), 이동평균, VWAP"""


def sma(closes: list[float], period: int) -> list[float]:
    """단순 이동평균"""
    result = [0.0] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1: i + 1]) / period
    return result


def ema(closes: list[float], period: int) -> list[float]:
    """지수 이동평균"""
    result = [0.0] * len(closes)
    if len(closes) < period:
        return result
    k = 2 / (period + 1)
    result[period - 1] = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(closes: list[float], period: int = 2) -> list[float]:
    """RSI 계산 (Wilder's smoothing)"""
    result = [50.0] * len(closes)
    if len(closes) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """ADX 계산"""
    n = len(closes)
    result = [0.0] * n
    if n < period * 2:
        return result

    tr_list = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        tr_list[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        plus_dm[i] = h_diff if h_diff > l_diff and h_diff > 0 else 0
        minus_dm[i] = l_diff if l_diff > h_diff and l_diff > 0 else 0

    # Wilder smoothing
    atr = sum(tr_list[1:period + 1]) / period
    plus_di_sum = sum(plus_dm[1:period + 1]) / period
    minus_di_sum = sum(minus_dm[1:period + 1]) / period

    dx_list = []
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_di_sum = (plus_di_sum * (period - 1) + plus_dm[i]) / period
        minus_di_sum = (minus_di_sum * (period - 1) + minus_dm[i]) / period

        if atr == 0:
            continue
        plus_di = 100 * plus_di_sum / atr
        minus_di = 100 * minus_di_sum / atr
        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0
        dx_list.append((i, dx))

    if len(dx_list) < period:
        return result

    # ADX = DX의 이동평균
    adx_val = sum(d for _, d in dx_list[:period]) / period
    for j in range(period, len(dx_list)):
        idx, dx_val = dx_list[j]
        adx_val = (adx_val * (period - 1) + dx_val) / period
        result[idx] = adx_val

    return result


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """ATR (Average True Range) — Wilder smoothing"""
    n = len(closes)
    result = [0.0] * n
    if n < period + 1:
        return result

    # True Range
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # 초기 ATR = 단순 평균
    atr_val = sum(tr[1:period + 1]) / period
    result[period] = atr_val

    # Wilder smoothing
    for i in range(period + 1, n):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
        result[i] = atr_val

    return result


def bollinger_bands(closes: list[float], period: int = 20, std_mult: float = 2.0
                    ) -> tuple[list[float], list[float], list[float]]:
    """볼린저 밴드 (upper, middle, lower)"""
    n = len(closes)
    upper = [0.0] * n
    middle = [0.0] * n
    lower = [0.0] * n

    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        middle[i] = mean
        upper[i] = mean + std_mult * std
        lower[i] = mean - std_mult * std

    return upper, middle, lower


def bollinger_bandwidth(closes: list[float], period: int = 20, std_mult: float = 2.0) -> list[float]:
    """볼린저 밴드폭 = (upper - lower) / middle"""
    upper, middle, lower = bollinger_bands(closes, period, std_mult)
    n = len(closes)
    result = [0.0] * n
    for i in range(n):
        if middle[i] > 0:
            result[i] = (upper[i] - lower[i]) / middle[i]
    return result


def find_support_resistance(highs: list[float], lows: list[float], closes: list[float],
                            tolerance: float = 0.015) -> dict:
    """지지/저항선 계산 + 터치 횟수

    60~90일 데이터에서 지지선(최근 저점 클러스터)과 저항선(최근 고점 클러스터)을 찾고,
    각 수준에 몇 번 터치했는지 카운트.

    Returns: {support, resistance, support_touches, resistance_touches, box_width_pct}
    """
    if len(closes) < 20:
        return {}

    # 최근 데이터의 고점/저점 수집
    period_high = max(highs)
    period_low = min(lows)

    if period_low <= 0:
        return {}

    # 지지선: 저점들의 클러스터 중심
    low_levels = _find_price_clusters(lows, tolerance)
    high_levels = _find_price_clusters(highs, tolerance)

    if not low_levels or not high_levels:
        return {}

    # 가장 많이 터치된 저점/고점 클러스터
    support_level, support_touches = low_levels[0]
    resistance_level, resistance_touches = high_levels[0]

    # 지지 > 저항이면 스왑 (비정상)
    if support_level >= resistance_level:
        return {}

    box_width_pct = (resistance_level - support_level) / support_level * 100

    return {
        "support": round(support_level, 2),
        "resistance": round(resistance_level, 2),
        "support_touches": support_touches,
        "resistance_touches": resistance_touches,
        "box_width_pct": round(box_width_pct, 2),
    }


def _find_price_clusters(prices: list[float], tolerance: float) -> list[tuple[float, int]]:
    """가격 리스트에서 클러스터(비슷한 가격대 그룹) 찾기

    로컬 극값만 추출한 뒤, 허용오차 내 가격을 하나의 클러스터로 묶는다.
    Returns: [(클러스터 중심, 터치 횟수)] — 터치 횟수 내림차순
    """
    if not prices or len(prices) < 3:
        return []

    # 로컬 극값 찾기 (전후보다 작거나 큰 점만)
    extremes = []
    for i in range(1, len(prices) - 1):
        if prices[i] <= prices[i - 1] and prices[i] <= prices[i + 1]:
            extremes.append(prices[i])  # 로컬 저점
        elif prices[i] >= prices[i - 1] and prices[i] >= prices[i + 1]:
            extremes.append(prices[i])  # 로컬 고점
    # 첫/마지막 값도 극값일 수 있음
    if len(prices) >= 2:
        if prices[0] <= prices[1] or prices[0] >= prices[1]:
            extremes.append(prices[0])
        if prices[-1] <= prices[-2] or prices[-1] >= prices[-2]:
            extremes.append(prices[-1])

    if not extremes:
        return []

    # 클러스터링 (허용오차 내 가격들을 하나의 그룹으로)
    extremes.sort()
    clusters = []
    current_cluster = [extremes[0]]

    for i in range(1, len(extremes)):
        center = sum(current_cluster) / len(current_cluster)
        if abs(extremes[i] - center) / center <= tolerance:
            current_cluster.append(extremes[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [extremes[i]]
    clusters.append(current_cluster)

    # 터치 횟수 순 정렬
    result = [(sum(c) / len(c), len(c)) for c in clusters]
    result.sort(key=lambda x: x[1], reverse=True)

    return result


def box_position_pct(price: float, support: float, resistance: float) -> float:
    """현재가의 박스 내 위치 (0%=지지선, 100%=저항선)"""
    if resistance <= support:
        return 50.0
    return (price - support) / (resistance - support) * 100


def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[int]) -> list[float]:
    """VWAP (당일 기준)"""
    result = [0.0] * len(closes)
    cum_vol = 0
    cum_tp_vol = 0.0
    for i in range(len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_vol += volumes[i]
        cum_tp_vol += tp * volumes[i]
        result[i] = cum_tp_vol / cum_vol if cum_vol > 0 else 0
    return result
