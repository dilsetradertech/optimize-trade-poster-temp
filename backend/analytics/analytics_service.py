from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import List
from .models import Trade


def make_aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def calculate_roi(trade: Trade) -> float:
    entry = trade.entryprice

    if trade.t3_hit:
        exit_price = trade.target3
    elif trade.t2_hit:
        exit_price = trade.target2
    elif trade.t1_hit:
        exit_price = trade.target1
    elif trade.stoploss_hit:
        exit_price = trade.stoploss
    elif trade.partial_profit > 0:
        return (trade.partial_profit / entry) * 100
    elif trade.partial_loss > 0:
        return (-trade.partial_loss / entry) * 100
    else:
        return 0

    if trade.position_type == "SHORT":
        return ((entry - exit_price) / entry) * 100
    else:
        return ((exit_price - entry) / entry) * 100


def format_time(sec):
    if not sec:
        return "00:00:00"
    sec = int(sec)
    return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"


def calculate_analytics(trades: List[Trade]):

    today = datetime.now().date()

    current_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    previous_days = [today - timedelta(days=i) for i in range(13, 6, -1)]

    def process(days):
        total, wins, sl, be, roi = 0, 0, 0, 0, 0
        daily_data = []

        t1_list, t2_list, t3_list, sl_list, duration_list = [], [], [], [], []

        def safe_add(lst, sec):
            if sec and sec > 0:
                lst.append(sec)

        for d in days:
            day_trades = [t for t in trades if t.created_at.date() == d]

            t_count, t_win, t_sl, t_be, t_roi = 0, 0, 0, 0, 0

            for t in day_trades:
                t_count += 1
                r = calculate_roi(t)
                t_roi += r

                is_target = t.t1_hit or t.t2_hit or t.t3_hit
                is_sl = t.stoploss_hit

                if is_target:
                    t_win += 1
                elif is_sl:
                    t_sl += 1
                else:
                    t_be += 1

                if t.t1_hit and t.t1_hit_at:
                    safe_add(t1_list, (make_aware(t.t1_hit_at) - make_aware(t.created_at)).total_seconds())

                if t.t2_hit and t.t2_hit_at:
                    safe_add(t2_list, (make_aware(t.t2_hit_at) - make_aware(t.created_at)).total_seconds())

                if t.t3_hit and t.t3_hit_at:
                    safe_add(t3_list, (make_aware(t.t3_hit_at) - make_aware(t.created_at)).total_seconds())

                if t.stoploss_hit and t.stoploss_hit_at:
                    safe_add(sl_list, (make_aware(t.stoploss_hit_at) - make_aware(t.created_at)).total_seconds())

                exit_time = (
                    t.t3_hit_at or
                    t.t2_hit_at or
                    t.t1_hit_at or
                    t.stoploss_hit_at
                )

                if exit_time:
                    safe_add(duration_list, (make_aware(exit_time) - make_aware(t.created_at)).total_seconds())

            total += t_count
            wins += t_win
            sl += t_sl
            be += t_be
            roi += t_roi

            win_rate = (t_win / t_count * 100) if t_count else 0

            daily_data.append({
                "day": f"{d.strftime('%A')} ({d.strftime('%d %b')})",
                "date": d.strftime("%Y-%m-%d"),
                "recommendations": t_count,
                "targetsHit": t_win,
                "stoplossHit": t_sl,
                "breakEven": t_be,
                "winRate": round(win_rate, 2),
                "roi": round(t_roi, 2)
            })

        def avg(lst):
            return sum(lst) / len(lst) if lst else 0

        return {
            "total": total,
            "wins": wins,
            "sl": sl,
            "be": be,
            "roi": roi,
            "daily": daily_data,
            "t1": avg(t1_list),
            "t2": avg(t2_list),
            "t3": avg(t3_list),
            "sl_time": avg(sl_list),
            "duration": avg(duration_list)
        }

    current = process(current_days)
    previous = process(previous_days)

    def change(curr, prev):
        return 0 if prev == 0 else round(((curr - prev) / prev) * 100, 2)

    def increased(curr, prev):
        return curr > prev

    win_rate = (current["wins"] / current["total"] * 100) if current["total"] else 0
    prev_win_rate = (previous["wins"] / previous["total"] * 100) if previous["total"] else 0

    # 🔥 BEST / WORST DAY
    valid_days = [d for d in current["daily"] if d["recommendations"] > 0]

    best_day = max(valid_days, key=lambda x: x["winRate"]) if valid_days else {}
    worst_day = min(valid_days, key=lambda x: x["winRate"]) if valid_days else {}

    # 🔥 BEST INSTRUMENT
    instrument_map = defaultdict(list)
    for t in trades:
        if t.created_at.date() in current_days:
            instrument_map[t.scrip].append(t)

    best_instrument = {"name": "N/A"}

    if instrument_map:
        best_score = float("-inf")
        for scrip, t_list in instrument_map.items():
            score = sum(calculate_roi(t) for t in t_list)
            if score > best_score:
                best_score = score
                best_instrument = {
                    "name": scrip,
                    "performance": "Best ROI",
                    "roi": round(score, 2)
                }

    return {
        "statsData": {
            "recommendations": {
                "value": current["total"],
                "changePercent": change(current["total"], previous["total"]),
                "increased": increased(current["total"], previous["total"]),
                "label": "Weekly Recommendations"
            },
            "targetHits": {
                "value": current["wins"],
                "changePercent": change(current["wins"], previous["wins"]),
                "increased": increased(current["wins"], previous["wins"]),
                "label": "Target Hits"
            },
            "stopLossHits": {
                "value": current["sl"],
                "changePercent": change(current["sl"], previous["sl"]),
                "increased": increased(current["sl"], previous["sl"]),
                "label": "Stop-Loss Hits"
            },
            "breakEven": {
                "value": current["be"],
                "changePercent": change(current["be"], previous["be"]),
                "increased": increased(current["be"], previous["be"]),
                "label": "Break Even"
            },
            "winRate": {
                "value": round(win_rate, 2),
                "changePercent": change(win_rate, prev_win_rate),
                "increased": increased(win_rate, prev_win_rate),
                "label": "Win Rate",
                "isPercentage": True
            },
            "roi": {
                "value": round(current["roi"], 2),
                "changePercent": change(current["roi"], previous["roi"]),
                "increased": increased(current["roi"], previous["roi"]),
                "label": "Total Weekly ROI",
                "isPercentage": True
            },
            "avgTime": {
                "t1": {"value": format_time(current["t1"]), "label": "Avg T1 Time"},
                "t2": {"value": format_time(current["t2"]), "label": "Avg T2 Time"},
                "t3": {"value": format_time(current["t3"]), "label": "Avg T3 Time"},
                "sl": {"value": format_time(current["sl_time"]), "label": "Avg SL Time"},
                "duration": {"value": format_time(current["duration"]), "label": "Avg Trade Duration"}
            }
        },
        "weeklyData": current["daily"],
        "tradingSummary": {
            "mostProfitableDay": {
                "day": best_day.get("day", "N/A"),
                "percentage": f"{best_day.get('winRate', 0)}%"
            },
            "worstTradingDay": {
                "day": worst_day.get("day", "N/A"),
                "percentage": f"{worst_day.get('winRate', 0)}%"
            },
            "bestInstrument": best_instrument
        }
    }