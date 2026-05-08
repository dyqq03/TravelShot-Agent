from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPOT_DIR = ROOT / "db" / "seed" / "spots"
DEFAULT_CACHE_FILE = ROOT / "db" / "seed" / ".nominatim_cache.json"
DEFAULT_ENDPOINT = "https://nominatim.openstreetmap.org/search"

CHINA_CITY_ALIASES = {
    "北京": "北京市",
    "杭州": "杭州市",
    "南京": "南京市",
    "青岛": "青岛市",
    "厦门": "厦门市",
    "三亚": "三亚市",
}

LANDMARK_ALIASES = {
    "故宫角楼": ["故宫角楼", "故宫博物院", "东华门", "神武门"],
    "筒子河": ["故宫角楼", "东华门", "神武门"],
    "景山万春亭": ["景山公园", "万春亭"],
    "北海公园白塔": ["北海公园", "琼华岛", "五龙亭"],
    "天坛祈年殿": ["天坛公园", "祈年殿"],
    "国家大剧院": ["国家大剧院"],
    "国贸CBD": ["国贸桥", "中国尊", "中央电视台总部大楼"],
    "中国尊": ["中国尊", "国贸桥", "中央电视台总部大楼"],
    "三里屯太古里": ["三里屯太古里", "三里屯"],
    "798": ["798艺术区"],
    "首钢园": ["首钢园", "三高炉", "滑雪大跳台"],
    "红砖美术馆": ["红砖美术馆"],
    "雍和宫": ["雍和宫"],
    "鼓楼": ["北京鼓楼", "北京钟楼", "钟鼓楼广场"],
    "钟楼": ["北京钟楼", "北京鼓楼", "钟鼓楼广场"],
    "什刹海": ["什刹海", "银锭桥", "后海"],
    "银锭桥": ["银锭桥", "什刹海"],
    "颐和园": ["颐和园", "十七孔桥", "佛香阁"],
    "慕田峪": ["慕田峪长城"],
    "午门": ["故宫博物院", "午门"],
    "太和门": ["故宫博物院", "太和门"],
    "前门": ["前门大街", "正阳门"],
    "正阳门": ["正阳门", "前门大街"],
    "鸟巢": ["国家体育场", "奥林匹克公园"],
    "水立方": ["国家游泳中心", "奥林匹克公园"],
    "中央电视塔": ["中央电视塔", "玉渊潭公园"],
    "玉渊潭": ["玉渊潭公园", "中央电视塔"],
    "曲院风荷": ["曲院风荷", "西湖曲院风荷"],
    "柳浪闻莺": ["柳浪闻莺"],
    "西泠桥": ["西泠桥", "北山街"],
    "断桥": ["断桥", "白堤"],
    "苏堤": ["苏堤", "花港观鱼"],
    "茅家埠": ["茅家埠"],
    "栈桥": ["青岛栈桥", "回澜阁"],
    "小青岛": ["小青岛公园", "小青岛灯塔"],
    "琴屿路": ["琴屿路", "小青岛公园"],
    "小鱼山": ["小鱼山公园"],
    "信号山": ["信号山公园"],
    "鸡鸣寺": ["鸡鸣寺", "台城"],
    "玄武湖": ["玄武湖公园"],
    "夫子庙": ["夫子庙", "秦淮河"],
    "老门东": ["老门东"],
    "椰梦长廊": ["椰梦长廊", "三亚湾"],
    "鹿回头": ["鹿回头风景区"],
    "黄厝": ["黄厝海滩"],
    "曾厝垵": ["曾厝垵"],
    "沙坡尾": ["沙坡尾"],
    "演武大桥": ["演武大桥观景平台"],
}

QUERY_SPLIT_PATTERN = re.compile(r"[-—_/／、，,;；|｜\s]+|及|与|和|至|到")
GENERIC_CANDIDATES = {
    "东侧",
    "西侧",
    "南侧",
    "北侧",
    "北岸",
    "南岸",
    "东岸",
    "西岸",
    "湖面",
    "水面",
    "山顶",
    "半山腰",
}
NOISE_WORDS = [
    "周边",
    "附近",
    "一带",
    "沿线",
    "方向",
    "入口",
    "出口",
    "外侧",
    "内外",
    "院内",
    "园内",
    "路段",
    "公交站牌",
    "观景点",
    "观景台",
    "拍",
    "俯",
    "俯拍",
    "同框",
    "倒影",
    "日落",
    "夜景",
    "人像",
    "街拍",
    "中轴对称",
    "金光穿洞",
    "最美转角",
    "高位",
    "视角",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Geocode db/seed/spots JSONL files with Nominatim. "
            "Default mode is dry-run; pass --write to update JSONL files."
        )
    )
    parser.add_argument("--spot-data-dir", default=str(DEFAULT_SPOT_DIR), help="Seed spots directory.")
    parser.add_argument("--pattern", default="spots_*.jsonl", help="JSONL glob pattern.")
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE_FILE), help="Local Nominatim cache JSON file.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Nominatim search endpoint.")
    parser.add_argument("--user-agent", default=None, help="Required Nominatim User-Agent identifying this app.")
    parser.add_argument("--email", default=os.getenv("NOMINATIM_EMAIL"), help="Optional contact email for Nominatim requests.")
    parser.add_argument("--sleep-seconds", type=float, default=1.1, help="Delay between uncached Nominatim requests.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum spots to process.")
    parser.add_argument("--city", default=None, help="Only process one city.")
    parser.add_argument("--force", action="store_true", help="Re-query spots that already have geo_verified=true.")
    parser.add_argument("--write", action="store_true", help="Write updated JSONL files.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files before writing.")
    parser.add_argument("--country-codes", default="cn", help="Nominatim countrycodes parameter.")
    parser.add_argument("--accept-language", default="zh-CN,zh;q=0.9,en;q=0.4", help="Accept-Language header.")
    parser.add_argument("--max-queries-per-spot", type=int, default=10, help="Maximum query variants per spot.")
    parser.add_argument("--show-queries", action="store_true", help="Print query variants and skip Nominatim requests.")
    return parser.parse_args()


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} is not valid JSONL") from exc
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]], create_backup: bool) -> None:
    if create_backup:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    payload = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ": ")) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def _add_unique(values: list[str], value: str) -> None:
    normalized = " ".join(value.split()).strip()
    if normalized and normalized not in values:
        values.append(normalized)


def _strip_noise(value: str) -> str:
    cleaned = value
    for word in NOISE_WORDS:
        cleaned = cleaned.replace(word, "")
    cleaned = re.sub(r"[()（）\[\]【】]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -—_")


def _split_location_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    for raw_part in QUERY_SPLIT_PATTERN.split(value):
        part = _strip_noise(raw_part)
        if len(part) < 2 or part in GENERIC_CANDIDATES:
            continue
        _add_unique(candidates, part)
        if part.endswith(("路", "街", "桥", "门", "园", "寺", "湖", "塔", "山", "巷", "湾", "滩")):
            continue
        shortened = re.sub(r"(北侧|南侧|东侧|西侧|山顶|半山腰|外|内|边)$", "", part)
        if shortened != part and len(shortened) >= 2:
            _add_unique(candidates, shortened)
    return candidates


def _alias_candidates(spot: dict[str, Any]) -> list[str]:
    haystack = " ".join(
        [
            str(spot.get("name") or ""),
            str(spot.get("location_hint") or ""),
            " ".join(spot.get("themes") or []),
        ]
    )
    candidates: list[str] = []
    for keyword, aliases in LANDMARK_ALIASES.items():
        if keyword in haystack:
            for alias in aliases:
                _add_unique(candidates, alias)
    return candidates


def _generic_candidates(spot: dict[str, Any]) -> list[str]:
    name = spot.get("name") or ""
    hint = spot.get("location_hint") or ""
    candidates: list[str] = []

    for alias in _alias_candidates(spot):
        _add_unique(candidates, alias)
    for item in _split_location_candidates(hint):
        _add_unique(candidates, item)
    for item in _split_location_candidates(name):
        _add_unique(candidates, item)

    name_core = _strip_noise(re.split(r"[-—_/／、与和及]", name, maxsplit=1)[0])
    if len(name_core) >= 2:
        _add_unique(candidates, name_core)
    hint_core = _strip_noise(re.split(r"[/／、，,;；及与和至到]", hint, maxsplit=1)[0])
    if len(hint_core) >= 2:
        _add_unique(candidates, hint_core)
    return candidates


def _queries_for_spot(spot: dict[str, Any], max_queries: int | None = None) -> list[str]:
    city = spot.get("city") or ""
    city_name = CHINA_CITY_ALIASES.get(city, city)
    name = spot.get("name") or ""
    hint = spot.get("location_hint") or ""
    candidates = _generic_candidates(spot)
    queries = []
    for candidate in candidates:
        _add_unique(queries, f"{candidate} {city_name} 中国")
    _add_unique(queries, f"{hint} {city_name} 中国")
    _add_unique(queries, f"{name} {city_name} 中国")
    _add_unique(queries, f"{name} {hint} {city_name} 中国")
    seen = set()
    compacted = []
    for query in queries:
        value = " ".join(query.split())
        if value and value not in seen:
            compacted.append(value)
            seen.add(value)
    if max_queries is not None and max_queries > 0:
        return compacted[:max_queries]
    return compacted


def _request_nominatim(
    *,
    endpoint: str,
    query: str,
    user_agent: str,
    email: str | None,
    country_codes: str,
    accept_language: str,
) -> list[dict[str, Any]]:
    params = {
        "format": "jsonv2",
        "q": query,
        "limit": 3,
        "addressdetails": 1,
        "countrycodes": country_codes,
    }
    if email:
        params["email"] = email
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Language": accept_language,
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _out_of_china(lat: float, lng: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(lng: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    if _out_of_china(lat, lng):
        return lat, lng
    a = 6378245.0
    ee = 0.00669342162296594323
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + d_lat, lng + d_lng


def _best_result(results: list[dict[str, Any]], spot: dict[str, Any]) -> dict[str, Any] | None:
    if not results:
        return None
    city = spot.get("city") or ""
    city_alias = CHINA_CITY_ALIASES.get(city, city)
    name = spot.get("name") or ""

    def score(item: dict[str, Any]) -> float:
        display_name = str(item.get("display_name") or "")
        value = float(item.get("importance") or 0)
        if city and city in display_name:
            value += 1.0
        if city_alias and city_alias in display_name:
            value += 1.0
        if name and name in display_name:
            value += 0.5
        return value

    return max(results, key=score)


def _apply_geocode(spot: dict[str, Any], query: str, result: dict[str, Any]) -> dict[str, Any]:
    wgs_lat = float(result["lat"])
    wgs_lng = float(result["lon"])
    gcj_lat, gcj_lng = _wgs84_to_gcj02(wgs_lat, wgs_lng)
    updated = dict(spot)
    updated.update(
        {
            "lat": round(gcj_lat, 6),
            "lng": round(gcj_lng, 6),
            "wgs84_lat": round(wgs_lat, 6),
            "wgs84_lng": round(wgs_lng, 6),
            "geo_verified": True,
            "geocode_source": "nominatim",
            "geocode_query": query,
            "geocode_display_name": result.get("display_name"),
            "geocode_importance": result.get("importance"),
            "geocode_osm_type": result.get("osm_type"),
            "geocode_osm_id": result.get("osm_id"),
            "geocode_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return updated


def _cache_key(query: str, country_codes: str) -> str:
    return json.dumps({"query": query, "countrycodes": country_codes}, ensure_ascii=False, sort_keys=True)


def _geocode_spot(
    spot: dict[str, Any],
    *,
    args: argparse.Namespace,
    cache: dict[str, Any],
    user_agent: str,
    last_request_at: list[float],
) -> tuple[dict[str, Any], bool, str]:
    if spot.get("geo_verified") and spot.get("lat") and spot.get("lng") and not args.force:
        return spot, False, "skip_existing"

    for query in _queries_for_spot(spot, args.max_queries_per_spot):
        key = _cache_key(query, args.country_codes)
        if key not in cache:
            elapsed = time.monotonic() - last_request_at[0]
            if last_request_at[0] and elapsed < args.sleep_seconds:
                time.sleep(args.sleep_seconds - elapsed)
            try:
                cache[key] = _request_nominatim(
                    endpoint=args.endpoint,
                    query=query,
                    user_agent=user_agent,
                    email=args.email,
                    country_codes=args.country_codes,
                    accept_language=args.accept_language,
                )
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                cache[key] = {"error": str(exc)}
            last_request_at[0] = time.monotonic()

        cached = cache[key]
        if isinstance(cached, dict) and cached.get("error"):
            continue
        result = _best_result(cached if isinstance(cached, list) else [], spot)
        if result and result.get("lat") and result.get("lon"):
            return _apply_geocode(spot, query, result), True, "matched"

    return spot, False, "not_found"


def main() -> None:
    args = _parse_args()
    spot_dir = Path(args.spot_data_dir)
    cache_file = Path(args.cache_file)
    user_agent = args.user_agent or "TravelShotAgentSeedGeocoder/0.1 (https://example.local/travelshot-agent)"
    cache = _load_cache(cache_file)
    last_request_at = [0.0]
    processed = 0
    updated_count = 0
    not_found_count = 0

    paths = sorted(spot_dir.glob(args.pattern))
    if not paths:
        print(f"No JSONL files found in {spot_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.write:
        print("Dry-run mode. Add --write to update JSONL files.")
    print("Nominatim policy reminder: single-threaded, cached, and limited to <= 1 request/second.")

    for path in paths:
        rows = _read_jsonl(path)
        next_rows = []
        file_updated = False
        for spot in rows:
            if args.city and spot.get("city") != args.city:
                next_rows.append(spot)
                continue
            if args.limit is not None and processed >= args.limit:
                next_rows.append(spot)
                continue
            processed += 1
            if args.show_queries:
                print(f"[queries] {path.name} {spot.get('city')} {spot.get('name')}")
                for query in _queries_for_spot(spot, args.max_queries_per_spot):
                    print(f"  - {query}")
                next_rows.append(spot)
                continue
            updated, changed, status = _geocode_spot(
                spot,
                args=args,
                cache=cache,
                user_agent=user_agent,
                last_request_at=last_request_at,
            )
            next_rows.append(updated)
            if changed:
                file_updated = True
                updated_count += 1
                print(
                    f"[matched] {path.name} {spot.get('city')} {spot.get('name')} "
                    f"via \"{updated.get('geocode_query')}\" -> {updated.get('lat')},{updated.get('lng')}"
                )
            elif status == "not_found":
                not_found_count += 1
                print(f"[not_found] {path.name} {spot.get('city')} {spot.get('name')}")

        if args.write and file_updated:
            _write_jsonl(path, next_rows, create_backup=not args.no_backup)

    _save_cache(cache_file, cache)
    mode = "updated" if args.write else "would update"
    print(f"Processed {processed} spots; {mode} {updated_count}; not found {not_found_count}.")


if __name__ == "__main__":
    main()
