# Seed Data

Seed 机位库位于 `db/seed/spots/*.jsonl`，已覆盖杭州、青岛、厦门等城市。

API 启动时默认会根据 `IMPORT_SEED_SPOTS_ON_STARTUP=true` 自动导入 `photo_spots`。
也可以手动执行：

```bash
python db/scripts/import_photo_spots.py
```

补全经纬度：

```bash
python db/scripts/geocode_seed_spots.py --city 杭州 --limit 3
python db/scripts/geocode_seed_spots.py --write
python db/scripts/import_photo_spots.py
```

`geocode_seed_spots.py` 默认 dry-run；加 `--write` 才会更新 JSONL。脚本使用 Nominatim 公共服务时会单线程、缓存请求，并按默认 1.1 秒间隔限速。Nominatim 返回 WGS84 坐标，脚本会额外写入转换后的 `lat`/`lng` 供高德路线规划使用，并保留 `wgs84_lat`/`wgs84_lng`。

如果具体拍摄描述搜不到，可以先查看脚本生成的降级查询词：

```bash
python db/scripts/geocode_seed_spots.py --city 北京 --limit 5 --show-queries
```

脚本会优先用真实 POI/地标名搜索，例如把“故宫角楼-筒子河倒影”降级为“故宫角楼、故宫博物院、东华门、神武门”等查询。

导入时会保留 `source_urls`、`confidence`、`geo_verified` 等字段作为数据质量依据。当前 seed 若缺少精确经纬度，地图路线会按同片区缓冲时间提示。
