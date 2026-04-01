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
