"""리스크 매니저 — 포지션 관리, 손절/익절, 일일/주간/월간 한도"""

import json
import os
import logging
from datetime import datetime, timedelta
import config

logger = logging.getLogger(__name__)

POSITIONS_FILE = os.path.join(config.DATA_DIR, "positions.json")
TRADES_FILE = os.path.join(config.DATA_DIR, "trades.json")
STATE_FILE = os.path.join(config.DATA_DIR, "state.json")


class RiskManager:
    def __init__(self):
        self.positions: list[dict] = []
        self.trades: list[dict] = []
        self.state: dict = {
            "daily_pnl": 0,
            "weekly_pnl": 0,
            "monthly_pnl": 0,
            "daily_trades": 0,
            "date": "",
            "week": "",
            "month": "",
            "cooldown": {},  # {symbol: expire_date}
        }
        self._load()

    def _load(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        for path, attr, default in [
            (POSITIONS_FILE, "positions", []),
            (TRADES_FILE, "trades", []),
            (STATE_FILE, "state", None),
        ]:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        val = json.load(f)
                    if default is None:
                        self.state.update(val)
                    else:
                        setattr(self, attr, val)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"손상된 파일 무시: {path} ({e})")
        self._reset_if_new_period()

    def _save(self):
        with open(POSITIONS_FILE, "w") as f:
            json.dump(self.positions, f, ensure_ascii=False, indent=2)
        with open(TRADES_FILE, "w") as f:
            json.dump(self.trades[-200:], f, ensure_ascii=False, indent=2)  # 최근 200건만 보관
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _reset_if_new_period(self):
        now = datetime.now()
        today = now.strftime("%Y%m%d")
        week = now.strftime("%Y-W%W")
        month = now.strftime("%Y%m")

        if self.state.get("date") != today:
            self.state["daily_pnl"] = 0
            self.state["daily_trades"] = 0
            self.state["us_daily_pnl"] = 0
            self.state["date"] = today
        if self.state.get("week") != week:
            self.state["weekly_pnl"] = 0
            self.state["us_weekly_pnl"] = 0
            self.state["week"] = week
        if self.state.get("month") != month:
            self.state["monthly_pnl"] = 0
            self.state["us_monthly_pnl"] = 0
            self.state["month"] = month

        # 만료된 쿨다운 제거
        expired = [s for s, d in self.state.get("cooldown", {}).items() if d <= today]
        for s in expired:
            del self.state["cooldown"][s]

    # ─── 포지션 관리 ───

    def add_position(self, symbol: str, qty: int, entry_price: float, strategy: str, entry_date: str, **kwargs):
        pos = {
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry_price,
            "high_price": entry_price,
            "strategy": strategy,
            "entry_date": entry_date,
        }
        pos.update(kwargs)
        self.positions.append(pos)
        self.state["daily_trades"] += 1
        self._save()

    def close_position(self, symbol: str, pnl: int, reason: str, strategy: str = None):
        if strategy:
            self.positions = [p for p in self.positions if not (p["symbol"] == symbol and p["strategy"] == strategy)]
        else:
            self.positions = [p for p in self.positions if p["symbol"] != symbol]
        self.state["daily_pnl"] += pnl
        self.state["weekly_pnl"] += pnl
        self.state["monthly_pnl"] += pnl
        self.state["daily_trades"] += 1
        # 미국 박스권 전략 별도 PnL 추적
        if strategy == "us_box":
            self.state["us_daily_pnl"] = self.state.get("us_daily_pnl", 0) + pnl
            self.state["us_weekly_pnl"] = self.state.get("us_weekly_pnl", 0) + pnl
            self.state["us_monthly_pnl"] = self.state.get("us_monthly_pnl", 0) + pnl

        # 손절이면 쿨다운 등록
        if pnl < 0:
            expire = (datetime.now() + timedelta(days=config.MAIN_COOLDOWN_DAYS)).strftime("%Y%m%d")
            self.state.setdefault("cooldown", {})[symbol] = expire

        self.trades.append({
            "symbol": symbol,
            "pnl": pnl,
            "reason": reason,
            "date": datetime.now().strftime("%Y%m%d %H:%M"),
        })
        self._save()
        logger.info(f"[리스크] 포지션 종료: {symbol} | 손익 {pnl:+,}원 | {reason} | 일일 누적 {self.state['daily_pnl']:+,}원")

    def get_positions(self, strategy: str = None) -> list[dict]:
        if strategy:
            return [p for p in self.positions if p["strategy"] == strategy]
        return self.positions

    def main_position_count(self) -> int:
        return len([p for p in self.positions if p["strategy"] == "ema"])

    # ─── 리스크 체크 ───

    def can_open_main_position(self) -> bool:
        if self.state["daily_pnl"] <= config.DAILY_MAX_LOSS:
            return False
        if self.state["weekly_pnl"] <= config.WEEKLY_MAX_LOSS:
            return False
        if self.state["monthly_pnl"] <= config.MONTHLY_MAX_LOSS:
            return False
        if self.state["daily_trades"] >= config.MAX_DAILY_TRADES:
            return False
        if self.main_position_count() >= config.MAIN_MAX_POSITIONS:
            return False
        return True

    def can_open_sub_position(self) -> bool:
        if self.state["daily_pnl"] <= config.DAILY_MAX_LOSS:
            return False
        if self.state["daily_trades"] >= config.MAX_DAILY_TRADES:
            return False
        # ETF 포지션은 동시에 1개만
        etf_positions = [p for p in self.positions if p["strategy"] == "etf"]
        return len(etf_positions) == 0

    def can_open_us_box_position(self) -> bool:
        """미국 박스권 전략 진입 가능 여부"""
        us_pnl = self.state.get("us_daily_pnl", 0)
        if us_pnl <= config.US_DAILY_MAX_LOSS:
            return False
        us_weekly = self.state.get("us_weekly_pnl", 0)
        if us_weekly <= config.US_WEEKLY_MAX_LOSS:
            return False
        us_monthly = self.state.get("us_monthly_pnl", 0)
        if us_monthly <= config.US_MONTHLY_MAX_LOSS:
            return False
        us_positions = [p for p in self.positions if p["strategy"] == "us_box"]
        return len(us_positions) < config.US_BOX_MAX_POSITIONS

    def is_in_cooldown(self, symbol: str) -> bool:
        today = datetime.now().strftime("%Y%m%d")
        expire = self.state.get("cooldown", {}).get(symbol, "")
        return expire > today

    # ─── 리포트 ───

    def daily_report(self) -> str:
        lines = [
            f"=== 일일 리포트 ({self.state['date']}) ===",
            f"일일 손익: {self.state['daily_pnl']:+,}원",
            f"주간 손익: {self.state['weekly_pnl']:+,}원",
            f"월간 손익: {self.state['monthly_pnl']:+,}원",
            f"오늘 매매: {self.state['daily_trades']}건",
            f"보유 포지션: {len(self.positions)}개",
        ]
        for p in self.positions:
            lines.append(f"  - {p['symbol']} {p['qty']}주 @ {p['entry_price']:,}원 ({p['strategy']})")

        # 최근 거래
        today_trades = [t for t in self.trades if t["date"].startswith(self.state["date"])]
        if today_trades:
            lines.append("오늘 거래:")
            for t in today_trades:
                lines.append(f"  - {t['symbol']} {t['pnl']:+,}원 ({t['reason']})")

        return "\n".join(lines)
