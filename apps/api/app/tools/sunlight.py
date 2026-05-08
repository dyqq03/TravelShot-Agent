from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.spot.cities import CITY_PROFILES, get_city_profile


def _sun_events(day: date, lat: float, lng: float) -> tuple[datetime, datetime]:
    from astral import Observer
    from astral.sun import sun

    tz = ZoneInfo("Asia/Shanghai")
    observer = Observer(latitude=lat, longitude=lng)
    events = sun(observer, date=day, tzinfo=tz)
    return events["sunrise"], events["sunset"]


def _fmt(value: datetime) -> str:
    return value.strftime("%H:%M")


def _float_coord(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sunlight_coordinates(parsed_goal: dict[str, Any]) -> tuple[float, float, str] | None:
    lat = _float_coord(parsed_goal.get("lat") or parsed_goal.get("latitude"))
    lng = _float_coord(parsed_goal.get("lng") or parsed_goal.get("longitude"))
    if lat is not None and lng is not None:
        return lat, lng, str(parsed_goal.get("coordinate_source") or "request_coordinates")

    city = parsed_goal.get("destination") or "杭州"
    if city in CITY_PROFILES:
        profile = get_city_profile(city)
        return float(profile["lat"]), float(profile["lng"]), "city_profile"
    return None


def build_sunlight_context(parsed_goal: dict[str, Any]) -> dict[str, Any]:
    city = parsed_goal.get("destination") or "杭州"
    dates = parsed_goal.get("date_range") or [date.today().isoformat()]
    coords = _sunlight_coordinates(parsed_goal)
    if coords is None:
        return {
            "status": "fallback",
            "calculation_source": "astral",
            "timezone": "Asia/Shanghai",
            "city": city,
            "observer": None,
            "daily": [],
            "summary": f"缺少{city}的经纬度，无法计算日出、日落和黄金时刻。",
            "error": f"缺少{city}的经纬度。",
        }
    lat, lng, coordinate_source = coords

    daily = []
    for raw_date in dates[:5]:
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            day = date.today()
        sunrise, sunset = _sun_events(day, lat, lng)
        morning_golden_start = sunrise + timedelta(minutes=20)
        morning_golden_end = sunrise + timedelta(minutes=85)
        evening_golden_start = sunset - timedelta(minutes=80)
        evening_golden_end = sunset + timedelta(minutes=5)
        blue_start = sunset + timedelta(minutes=10)
        blue_end = sunset + timedelta(minutes=40)
        daily.append(
            {
                "date": day.isoformat(),
                "sunrise": _fmt(sunrise),
                "sunset": _fmt(sunset),
                "golden_hours": [
                    {"type": "morning", "start": _fmt(morning_golden_start), "end": _fmt(morning_golden_end)},
                    {"type": "evening", "start": _fmt(evening_golden_start), "end": _fmt(evening_golden_end)},
                ],
                "blue_hour": {"start": _fmt(blue_start), "end": _fmt(blue_end)},
                "harsh_light_window": {"start": "11:00", "end": "14:30"},
            }
        )

    first = daily[0]
    return {
        "status": "calculated",
        "calculation_source": "astral",
        "timezone": "Asia/Shanghai",
        "city": city,
        "observer": {"lat": lat, "lng": lng},
        "coordinate_source": coordinate_source,
        "daily": daily,
        "summary": (
            f"{city}{first['date']} 日出 {first['sunrise']}，日落 {first['sunset']}；"
            f"傍晚黄金时刻约 {first['golden_hours'][1]['start']}-{first['golden_hours'][1]['end']}。"
        ),
    }
