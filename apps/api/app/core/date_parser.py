from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


WEEKDAY_INDEX = {
    "一": 0,
    "1": 0,
    "二": 1,
    "两": 1,
    "2": 1,
    "三": 2,
    "3": 2,
    "四": 3,
    "4": 3,
    "五": 4,
    "5": 4,
    "六": 5,
    "6": 5,
    "日": 6,
    "天": 6,
    "七": 6,
    "7": 6,
}
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
RANGE_SEP = r"(?:到|至|—|－|~|～)"
WEEK_UNIT_TOKENS = ("周", "星期", "礼拜")
WEEKDAY_CHARS = r"[一二三四五六日天七1-7]"
RANGE_CONNECTORS = {"到", "至", "—", "－", "~", "～"}
ENUM_CONNECTORS = {"和", "及", "与", "、", "，", ",", "/"}
WEEKDAY_CONNECTOR_PATTERN = r"(到|至|—|－|~|～|和|及|与|、|，|,|/)"
WEEK_PREFIX_PATTERN = (
    r"(下下个周|下下周|下下星期|下下个星期|下下礼拜|"
    r"下周|下个周|下星期|下个星期|下礼拜|"
    r"本周|这周|这个周|这星期|这个星期|周|星期|礼拜)"
)


def china_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def parse_user_date_range(text: str, today: date | None = None, *, default_today: bool = False) -> list[str]:
    base = today or china_today()
    compact = _compact(text)
    duration = parse_duration_days(text)

    parsed = _parse_iso_range(compact) or _parse_month_day_range(compact, base)
    if parsed:
        return _date_list(parsed[0], parsed[1])

    weekday_dates = _parse_weekday_dates(compact, base)
    if weekday_dates:
        if len(weekday_dates) == 1 and duration and duration > 1:
            return _expand_days(date.fromisoformat(weekday_dates[0]), duration)
        return weekday_dates

    weekend = _parse_weekend(compact, base, duration)
    if weekend:
        return weekend

    start = _parse_relative_day(compact, base) or _parse_relative_offset_day(compact, base)
    if start:
        if duration and duration > 1:
            return _expand_days(start, duration)
        return [start.isoformat()]

    start = _parse_month_day_single(compact, base)
    if start:
        if duration and duration > 1:
            return _expand_days(start, duration)
        return [start.isoformat()]

    start = _parse_week_or_month_start(compact, base)
    if start:
        if duration and duration > 1:
            return _expand_days(start, duration)
        if _is_week_start_signal(compact):
            return _expand_days(start, 7)
        return [start.isoformat()]

    return [base.isoformat()] if default_today else []


def parse_duration_days(text: str) -> int | None:
    compact = _compact(text)
    week_match = _duration_week_match(compact)
    if week_match and "周末" not in compact and "星期末" not in compact:
        weeks = _number_token_to_int(week_match.group(1))
        if weeks:
            return min(weeks * 7, 31)
    one_week_tokens = ("一周", "一星期", "一个星期", "七天", "7天")
    if any(_contains_duration_token(compact, token) for token in one_week_tokens) and not any(f"{token}后" in compact for token in one_week_tokens):
        return 7
    day_match = re.search(r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:天|日)(?!后|前)", compact)
    if day_match:
        days = _number_token_to_int(day_match.group(1))
        if days:
            return min(days, 31)
    return None


def _duration_week_match(compact: str) -> re.Match[str] | None:
    pattern = r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:个)?(?:周|星期|礼拜)(?!后|前)"
    for match in re.finditer(pattern, compact):
        # Avoid reading "下周二周三" / "周二周三" as "2 weeks".
        if compact[: match.start()].endswith(WEEK_UNIT_TOKENS):
            continue
        return match
    return None


def _contains_duration_token(compact: str, token: str) -> bool:
    start = compact.find(token)
    while start >= 0:
        if not compact[:start].endswith(WEEK_UNIT_TOKENS):
            return True
        start = compact.find(token, start + 1)
    return False


def _parse_iso_range(compact: str) -> tuple[date, date] | None:
    matches = [date.fromisoformat(match) for match in re.findall(r"\d{4}-\d{2}-\d{2}", compact)]
    if len(matches) >= 2:
        return _ordered(matches[0], matches[1])
    if len(matches) == 1:
        return matches[0], matches[0]
    return None


def _parse_weekday_dates(compact: str, base: date) -> list[str]:
    prefix_match = re.search(WEEK_PREFIX_PATTERN, compact)
    if not prefix_match:
        return []
    mentions = _weekday_mentions_after_prefix(compact[prefix_match.end():])
    if not mentions:
        return []

    prefix = prefix_match.group(1)
    week_start = _week_start(base, prefix)
    dates = [week_start + timedelta(days=int(item["weekday"])) for item in mentions]
    if prefix in WEEK_UNIT_TOKENS and dates[0] < base:
        dates = [item + timedelta(days=7) for item in dates]
    for index in range(1, len(dates)):
        while dates[index] < dates[index - 1]:
            dates[index] += timedelta(days=7)

    if _weekday_mentions_are_range(mentions):
        return _date_list(dates[0], dates[-1])
    return [item.isoformat() for item in dates]


def _weekday_mentions_after_prefix(tail: str) -> list[dict[str, object]]:
    mentions: list[dict[str, object]] = []
    pos = 0
    first = True
    while pos < len(tail):
        connector = ""
        if not first:
            connector_match = re.match(WEEKDAY_CONNECTOR_PATTERN, tail[pos:])
            if connector_match:
                connector = connector_match.group(1)
                pos += len(connector)

        unit = _week_unit_at(tail, pos)
        if unit:
            pos += len(unit)

        if pos >= len(tail):
            break
        weekday = WEEKDAY_INDEX.get(tail[pos])
        if weekday is None:
            break
        mentions.append({"weekday": weekday, "connector": connector, "had_unit": bool(unit)})
        pos += 1
        first = False
    return mentions


def _week_unit_at(text: str, pos: int) -> str:
    for unit in WEEK_UNIT_TOKENS:
        if text.startswith(unit, pos):
            return unit
    return ""


def _weekday_mentions_are_range(mentions: list[dict[str, object]]) -> bool:
    if len(mentions) < 2:
        return False
    connectors = [str(item.get("connector") or "") for item in mentions[1:]]
    if any(connector in RANGE_CONNECTORS for connector in connectors):
        return True
    if any(connector in ENUM_CONNECTORS for connector in connectors):
        return False
    return len(mentions) == 2 and bool(mentions[1].get("had_unit"))


def _parse_month_day_range(compact: str, base: date) -> tuple[date, date] | None:
    if "月" not in compact:
        return None
    relative_month = re.search(r"(下下个月|下个月|下月|本月|这个月|这月)([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)" + RANGE_SEP + r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)", compact)
    if relative_month:
        year, month = _relative_month(base, relative_month.group(1))
        start_day = _number_token_to_int(relative_month.group(2))
        end_day = _number_token_to_int(relative_month.group(3))
        start = _safe_date(year, month, start_day)
        end = _safe_date(year, month, end_day)
        if start and end:
            return _ordered(start, end)

    full = re.search(
        r"(?:(\d{4})年)?([0-9]{1,2})月([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)?"
        + RANGE_SEP
        + r"(?:(\d{4})年)?(?:(\d{1,2})月)?([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)?",
        compact,
    )
    if not full:
        return None
    start_year = int(full.group(1)) if full.group(1) else base.year
    start_month = int(full.group(2))
    start_day = _number_token_to_int(full.group(3))
    end_year = int(full.group(4)) if full.group(4) else start_year
    end_month = int(full.group(5)) if full.group(5) else start_month
    end_day = _number_token_to_int(full.group(6))
    start = _safe_date(start_year, start_month, start_day)
    end = _safe_date(end_year, end_month, end_day)
    if start and end:
        return _ordered(start, end)
    return None


def _parse_weekend(compact: str, base: date, duration: int | None) -> list[str]:
    offset_base = _parse_relative_offset_day(compact, base)
    if offset_base and any(token in compact for token in ("周末", "星期末", "礼拜末", "weekend")):
        saturday = _this_weekend_start(offset_base)
    elif any(token in compact for token in ("下下周末", "下下个周末", "下下星期末", "下下个星期末", "下下礼拜末")):
        saturday = _next_week_start(base) + timedelta(days=12)
    elif any(token in compact for token in ("下周末", "下个周末", "下星期末", "下个星期末", "nextweekend")):
        saturday = _next_week_start(base) + timedelta(days=5)
    elif any(token in compact for token in ("这周末", "本周末", "这个周末", "周末", "thisweekend")):
        saturday = _this_weekend_start(base)
    else:
        return []
    if duration and duration > 2:
        return _expand_days(saturday, duration)
    if saturday.weekday() == 6:
        return [saturday.isoformat()] if saturday >= base else []
    days = [saturday, saturday + timedelta(days=1)]
    return [item.isoformat() for item in days if item >= base]


def _parse_relative_day(compact: str, base: date) -> date | None:
    if "大后天" in compact:
        return base + timedelta(days=3)
    if "后天" in compact:
        return base + timedelta(days=2)
    if "明天" in compact:
        return base + timedelta(days=1)
    if "今天" in compact:
        return base
    return None


def _parse_relative_offset_day(compact: str, base: date) -> date | None:
    match = re.search(r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:个)?(天|日|周|星期|礼拜|月)后", compact)
    if not match:
        return None
    amount = _number_token_to_int(match.group(1))
    if not amount:
        return None
    unit = match.group(2)
    if unit in {"天", "日"}:
        return base + timedelta(days=amount)
    if unit in {"周", "星期", "礼拜"}:
        return base + timedelta(days=amount * 7)
    if unit == "月":
        year, month = _add_month(base.year, base.month, amount)
        return _safe_date(year, month, min(base.day, monthrange(year, month)[1]))
    return None


def _parse_month_day_single(compact: str, base: date) -> date | None:
    relative = re.search(r"(下下个月|下个月|下月|本月|这个月|这月)([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)", compact)
    if relative:
        year, month = _relative_month(base, relative.group(1))
        return _safe_date(year, month, _number_token_to_int(relative.group(2)))

    full = re.search(r"(?:(\d{4})年)?([0-9]{1,2})月([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)?", compact)
    if full:
        year = int(full.group(1)) if full.group(1) else base.year
        month = int(full.group(2))
        day = _number_token_to_int(full.group(3))
        candidate = _safe_date(year, month, day)
        if candidate and not full.group(1) and candidate < base:
            candidate = _safe_date(year + 1, month, day)
        return candidate

    day_only = re.search(r"(?<!月)([0-9]{1,2}|[一二两三四五六七八九十]{1,3})(?:号|日)", compact)
    if day_only:
        day = _number_token_to_int(day_only.group(1))
        candidate = _safe_date(base.year, base.month, day)
        if candidate and candidate < base:
            year, month = _add_month(base.year, base.month, 1)
            candidate = _safe_date(year, month, day)
        return candidate
    return None


def _parse_week_or_month_start(compact: str, base: date) -> date | None:
    if any(token in compact for token in ("下下周", "下下个周", "下下星期", "下下个星期", "下下礼拜", "下周", "下个周", "下星期", "下个星期", "下礼拜", "nextweek")):
        if any(token in compact for token in ("下下周", "下下个周", "下下星期", "下下个星期", "下下礼拜")):
            return _next_week_start(base) + timedelta(days=7)
        return _next_week_start(base)
    if any(token in compact for token in ("下下个月",)):
        year, month = _add_month(base.year, base.month, 2)
        return date(year, month, 1)
    if any(token in compact for token in ("下个月", "下月", "nextmonth")):
        year, month = _add_month(base.year, base.month, 1)
        return date(year, month, 1)
    return None


def _is_week_start_signal(compact: str) -> bool:
    return any(token in compact for token in ("下下周", "下下个周", "下下星期", "下下个星期", "下下礼拜", "下周", "下星期", "下礼拜", "nextweek")) and not any(token in compact for token in ("周末", "星期末", "礼拜末", "weekend"))


def _week_start(base: date, prefix: str) -> date:
    current_monday = base - timedelta(days=base.weekday())
    if prefix in {"下下周", "下下个周", "下下星期", "下下个星期", "下下礼拜"}:
        return current_monday + timedelta(days=14)
    if prefix in {"下周", "下个周", "下星期", "下个星期", "下礼拜"}:
        return current_monday + timedelta(days=7)
    if prefix in {"本周", "这周", "这个周", "这星期", "这个星期"}:
        return current_monday
    return current_monday


def _next_week_start(base: date) -> date:
    return base - timedelta(days=base.weekday()) + timedelta(days=7)


def _this_weekend_start(base: date) -> date:
    if base.weekday() <= 5:
        return base + timedelta(days=5 - base.weekday())
    return base


def _relative_month(base: date, token: str) -> tuple[int, int]:
    if token == "下下个月":
        return _add_month(base.year, base.month, 2)
    if token in {"下个月", "下月"}:
        return _add_month(base.year, base.month, 1)
    return base.year, base.month


def _add_month(year: int, month: int, offset: int) -> tuple[int, int]:
    zero_based = year * 12 + (month - 1) + offset
    return zero_based // 12, zero_based % 12 + 1


def _safe_date(year: int, month: int, day: int | None) -> date | None:
    if day is None or not (1 <= month <= 12):
        return None
    _, last_day = monthrange(year, month)
    if not (1 <= day <= last_day):
        return None
    return date(year, month, day)


def _date_list(start: date, end: date) -> list[str]:
    start, end = _ordered(start, end)
    days = (end - start).days + 1
    if days <= 0 or days > 31:
        return []
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


def _expand_days(start: date, duration: int) -> list[str]:
    duration = max(1, min(duration, 31))
    return [(start + timedelta(days=offset)).isoformat() for offset in range(duration)]


def _ordered(first: date, second: date) -> tuple[date, date]:
    return (first, second) if first <= second else (second, first)


def _number_token_to_int(token: str | None) -> int | None:
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + CN_DIGITS.get(token[1:], 0)
    if "十" in token:
        left, right = token.split("十", 1)
        tens = CN_DIGITS.get(left, 0)
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CN_DIGITS.get(token)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text.strip()).lower()
