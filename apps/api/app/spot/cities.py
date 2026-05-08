from __future__ import annotations

from datetime import date

from app.core.date_parser import china_today, parse_user_date_range


CITY_PROFILES: dict[str, dict] = {
    "杭州": {
        "lat": 30.246,
        "lng": 120.155,
        "arrival_station": "杭州东",
        "arrival_note": "从上海出发建议高铁到杭州东，再换乘地铁或打车进西湖片区。",
        "default_start_time": "15:00",
    },
    "青岛": {
        "lat": 36.067,
        "lng": 120.383,
        "arrival_station": "青岛站",
        "arrival_note": "海边机位分布较长，建议把栈桥、琴屿路、小青岛排成连续动线。",
        "default_start_time": "14:30",
    },
    "厦门": {
        "lat": 24.479,
        "lng": 118.089,
        "arrival_station": "厦门站",
        "arrival_note": "岛内机位适合用地铁、公交和打车组合，日落优先留给海边。",
        "default_start_time": "15:00",
    },
    "北京": {
        "lat": 39.904,
        "lng": 116.407,
        "arrival_station": "北京站",
        "arrival_note": "城市跨度大，建议只选一个片区做半日路线。",
        "default_start_time": "14:30",
    },
    "南京": {
        "lat": 32.060,
        "lng": 118.797,
        "arrival_station": "南京南",
        "arrival_note": "梧桐、城墙、秦淮河适合按片区拆线。",
        "default_start_time": "15:00",
    },
    "三亚": {
        "lat": 18.252,
        "lng": 109.512,
        "arrival_station": "三亚站",
        "arrival_note": "海边日晒强，优先避开正午硬光。",
        "default_start_time": "15:30",
    },
}


CITY_ALIASES = {
    "西湖": "杭州",
    "北山街": "杭州",
    "柳浪闻莺": "杭州",
    "曲院风荷": "杭州",
    "栈桥": "青岛",
    "琴屿路": "青岛",
    "小青岛": "青岛",
    "八大关": "青岛",
    "鼓浪屿": "厦门",
    "沙坡尾": "厦门",
    "黄厝": "厦门",
    "曾厝垵": "厦门",
    "筒子河": "北京",
    "故宫": "北京",
    "景山": "北京",
    "秦淮": "南京",
    "玄武湖": "南京",
    "亚龙湾": "三亚",
    "天涯海角": "三亚",
    "大小洞天": "三亚",
    "西岛": "三亚",
    "蜈支洲": "三亚",
}

COASTAL_DEFAULT_CITY = "青岛"
COASTAL_INTENT_KEYWORDS = ["海边", "沙滩", "海岸", "海景", "灯塔", "蓝天", "阳光感"]


KNOWN_DEPARTURE_CITIES = [
    "上海",
    "北京",
    "杭州",
    "南京",
    "苏州",
    "青岛",
    "厦门",
    "广州",
    "深圳",
    "成都",
    "武汉",
]


def infer_city(text: str) -> str:
    for city in CITY_PROFILES:
        if city in text:
            return city
    for keyword, city in CITY_ALIASES.items():
        if keyword in text:
            return city
    if any(keyword in text for keyword in COASTAL_INTENT_KEYWORDS):
        return COASTAL_DEFAULT_CITY
    return "待推荐"


def has_destination_signal(text: str) -> bool:
    if any(city in text for city in CITY_PROFILES):
        return True
    return any(keyword in text for keyword in CITY_ALIASES)


def infer_departure_city(text: str, destination: str) -> str | None:
    for city in KNOWN_DEPARTURE_CITIES:
        if city != destination and (f"从{city}" in text or f"{city}去" in text or f"{city}到" in text):
            return city
    return None


def parse_date_range(text: str, today: date | None = None) -> list[str]:
    return parse_user_date_range(text, today=today, default_today=True)


def get_city_profile(city: str) -> dict:
    return CITY_PROFILES.get(city, CITY_PROFILES["杭州"])
