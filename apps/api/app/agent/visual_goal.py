from __future__ import annotations


def build_visual_goal(
    parsed_goal: dict,
    image_analysis: dict | None = None,
    reference_clues: list[dict] | None = None,
) -> dict:
    styles = parsed_goal.get("shooting_style") or ["自然旅拍"]
    elements = parsed_goal.get("visual_elements") or []
    destination = parsed_goal.get("destination", "目的地")

    if "日系清新" in styles:
        interpretation = {
            "color": ["低饱和", "浅色衣物", "绿色或蓝色环境"],
            "lighting": ["柔和自然光", "侧光", "轻微逆光"],
            "composition": ["留白", "前景遮挡", "人物小比例环境人像"],
            "mood": ["安静", "松弛", "自然"],
        }
    elif "电影感" in styles:
        interpretation = {
            "color": ["低对比", "暖色或青绿色倾向", "环境色统一"],
            "lighting": ["侧逆光", "蓝调", "雨后反光"],
            "composition": ["引导线", "遮挡", "中近景叙事"],
            "mood": ["故事感", "克制", "有停顿"],
        }
    elif "国风" in styles or "古风" in styles:
        interpretation = {
            "color": ["素色", "红墙灰瓦", "低饱和"],
            "lighting": ["晨光", "斜侧光", "避开正午"],
            "composition": ["对称", "门洞框景", "长焦压缩"],
            "mood": ["端正", "含蓄", "古典"],
        }
    else:
        interpretation = {
            "color": ["干净", "统一", "保留环境主色"],
            "lighting": ["柔光", "黄金时刻", "阴天漫射光"],
            "composition": ["环境带人", "中景", "细节特写"],
            "mood": ["自然", "轻松", "生活感"],
        }

    image_elements = []
    if image_analysis:
        for key in ["visual_elements", "scene_elements", "objects", "location_types"]:
            value = image_analysis.get(key)
            if isinstance(value, list):
                image_elements.extend(str(item) for item in value if item)
            elif isinstance(value, str) and value.strip():
                image_elements.append(value.strip())
    clue_elements = []
    for clue in reference_clues or []:
        value = clue.get("visual_elements") if isinstance(clue, dict) else None
        if isinstance(value, list):
            clue_elements.extend(str(item) for item in value if item)

    must_have = list(dict.fromkeys([*elements, *image_elements, *clue_elements]))[:5] or ["自然光", "环境人像"]
    optional = []
    for item in ["夕阳", "蓝天", "倒影", "树荫", "长椅", "街道", "海浪", "湖面"]:
        if item not in must_have:
            optional.append(item)
    optional = optional[:4]

    goal = {
        "primary_goal": f"{destination}{'、'.join(styles[:2])}旅拍",
        "must_have_elements": must_have,
        "optional_elements": optional,
        "style_interpretation": interpretation,
        "weather_adaptation": {
            "if_sunny": "强化蓝天、发丝光、湖面/海面反光，注意降低曝光保护高光。",
            "if_cloudy": "强化柔和肤色、树荫、长椅和低对比清新感。",
            "if_rainy": "转为雨天电影感，寻找屋檐、街道反光和可停留的安全点。",
            "if_windy": "减少海边长时间停留，优先拍顺风走动、发丝动态和半身画面。",
        },
        "priority_shots": [
            f"{must_have[0]}环境人像",
            "自然走动",
            "半身情绪特写",
            "关键光线窗口的背影或回头",
        ],
    }
    if image_analysis:
        goal["reference_image"] = {
            "style_summary": image_analysis.get("style_summary") or image_analysis.get("description"),
            "lighting": image_analysis.get("lighting"),
            "composition": image_analysis.get("composition"),
            "pose_action": image_analysis.get("pose_action") or image_analysis.get("poses"),
            "possible_location_types": image_analysis.get("possible_location_types") or image_analysis.get("location_types"),
            "replication_notes": image_analysis.get("replication_notes"),
        }
        if image_analysis.get("shooting_style") and isinstance(image_analysis.get("shooting_style"), list):
            goal["style_interpretation"]["image_style"] = image_analysis["shooting_style"][:5]
    return goal
