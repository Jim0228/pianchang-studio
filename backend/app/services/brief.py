from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AppError
from ..models import Asset, Product, Project
from ..utils import json_dumps, json_loads, new_id, truncate

CATEGORY_RULES: dict[str, list[str]] = {
    "家居清洁": ["清洁", "油污", "洗衣", "除菌", "厨房", "浴室", "拖把", "洗洁精", "抹布", "收纳"],
    "美妆个护": ["面膜", "护肤", "口红", "精华", "洗发", "沐浴", "香水", "彩妆", "防晒", "身体乳"],
    "食品饮料": ["咖啡", "茶", "零食", "坚果", "水果", "牛奶", "食品", "饮料", "烘焙", "巧克力"],
    "3C数码": ["手机", "耳机", "平板", "充电", "数码", "电脑", "音箱", "键盘", "相机", "智能"],
    "服饰鞋包": ["T恤", "裙", "裤", "鞋", "包", "外套", "袜", "帽", "衬衫", "羽绒"],
    "母婴": ["宝宝", "婴儿", "儿童", "孕妇", "奶粉", "纸尿裤", "辅食", "玩具"],
    "户外运动": ["户外", "露营", "跑步", "健身", "瑜伽", "登山", "骑行", "运动"],
    "宠物": ["猫", "狗", "宠物", "猫粮", "狗粮", "猫砂"],
    "家居家纺": ["床品", "四件套", "枕头", "沙发", "地毯", "窗帘", "家具"],
}

PAIN_MAP = {
    "家居清洁": "厨房油污难清理",
    "美妆个护": "皮肤状态不稳定",
    "食品饮料": "想吃点好的又怕麻烦",
    "3C数码": "设备不好用还占地方",
    "服饰鞋包": "搭配费时间还不合身",
    "母婴": "给宝宝选东西总不放心",
    "户外运动": "想运动又懒得准备",
    "宠物": "毛孩子挑食又掉毛",
    "家居家纺": "家里总觉得差点质感",
}

AUDIENCE_MAP = {
    "家居清洁": "25-45 岁家庭用户",
    "美妆个护": "18-35 岁爱美人群",
    "食品饮料": "18-40 岁注重生活品质的人群",
    "3C数码": "18-35 岁数码爱好者",
    "服饰鞋包": "18-35 岁都市人群",
    "母婴": "0-6 岁孩子的家长",
    "户外运动": "20-40 岁运动人群",
    "宠物": "养宠家庭",
    "家居家纺": "25-45 岁居家用户",
}


def detect_category(text: str) -> str:
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_RULES.items():
        scores[category] = sum(1 for keyword in keywords if keyword in text)
    best = max(scores, key=scores.get) if scores else "家居清洁"
    return best if scores.get(best, 0) > 0 else "家居清洁"


def extract_selling_points(text: str, limit: int = 5) -> list[str]:
    parts = re.split(r"[，,。；;、\n\r]+", text or "")
    points: list[str] = []
    for part in parts:
        cleaned = part.strip(" \t-·•")
        if not cleaned or len(cleaned) < 2 or len(cleaned) > 30:
            continue
        if cleaned in points:
            continue
        points.append(cleaned)
        if len(points) >= limit:
            break
    return points or ["品质可靠", "使用方便", "性价比高"]


def mock_brief(project: Project) -> dict:
    text = f"{project.name}\n{project.description}".strip()
    first_line = next((line.strip() for line in (project.description or "").splitlines() if line.strip()), "")
    title = truncate(project.name.strip() or first_line or "新品好物", 40)
    category = detect_category(text)

    price_match = re.search(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|RMB|rmb|¥)", text)
    if price_match is None:
        price_match = re.search(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)", text)
    price = price_match.group(1) if price_match else ""
    points = extract_selling_points(project.description or text)

    keywords = [category, *points[:3]]
    return {
        "title": title,
        "category": category,
        "selling_points": points,
        "audience": AUDIENCE_MAP.get(category, "有相关需求的消费者"),
        "price": price,
        "tone": "真实、生活化、可信",
        "pain_point": PAIN_MAP.get(category, "选来选去总不满意"),
        "keywords": list(dict.fromkeys(keywords)),
        "risks": ["避免绝对化用语", "不得虚构参数与功效"],
        "source": "mock",
    }


def normalize_brief(data: dict, project: Project) -> dict:
    brief = dict(data or {})
    fallback = mock_brief(project)

    brief["title"] = truncate(str(brief.get("title") or fallback["title"]), 40)
    brief["category"] = str(brief.get("category") or fallback["category"])
    points = brief.get("selling_points")
    if not isinstance(points, list):
        points = fallback["selling_points"]
    points = [truncate(str(p).strip(), 30) for p in points if str(p).strip()]
    brief["selling_points"] = points[:5] or fallback["selling_points"]
    brief["audience"] = str(brief.get("audience") or fallback["audience"])
    brief["price"] = str(brief.get("price") or fallback["price"])
    brief["tone"] = str(brief.get("tone") or fallback["tone"])
    brief["pain_point"] = str(brief.get("pain_point") or fallback["pain_point"])
    keywords = brief.get("keywords")
    if not isinstance(keywords, list):
        keywords = fallback["keywords"]
    brief["keywords"] = [str(k) for k in keywords][:8]
    risks = brief.get("risks")
    if not isinstance(risks, list):
        risks = fallback["risks"]
    brief["risks"] = [str(r) for r in risks][:8]
    brief.setdefault("source", "mock")
    return brief


def get_brief(product: Product | None) -> dict:
    if product is None:
        return {}
    return json_loads(product.brief_json, {})


async def analyze_product(session: Session, project: Project, settings: Settings) -> Product:
    provider = settings.llm_provider_name
    if provider == "deepseek":
        if not settings.deepseek_configured:
            raise AppError("DEEPSEEK_KEY_MISSING", "DeepSeek 未配置 DEEPSEEK_API_KEY", status_code=400)
        from .providers.deepseek import DeepSeekClient

        client = DeepSeekClient(settings)
        data = await client.analyze(project.description, project.name)
        brief = normalize_brief(data, project)
        brief["source"] = "deepseek"
    elif provider == "ark":
        if not settings.ark_key_present:
            raise AppError("ARK_KEY_MISSING", "ark 模式需要配置 ARK_API_KEY", status_code=400)
        from .providers.ark import ArkClient

        original_assets = (
            session.query(Asset)
            .filter(Asset.project_id == project.id, Asset.kind == "original")
            .order_by(Asset.created_at.asc())
            .limit(3)
            .all()
        )
        image_paths = [settings.data_path / asset.path for asset in original_assets]
        client = ArkClient(settings)
        data = await client.vision_brief(image_paths, project.description, project.name)
        brief = normalize_brief(data, project)
        brief["source"] = "ark"
    else:
        brief = mock_brief(project)

    product = session.query(Product).filter(Product.project_id == project.id).first()
    if product is None:
        product = Product(id=new_id("prod"), project_id=project.id)
        session.add(product)
    product.brief_json = json_dumps(brief)
    product.title = brief["title"]
    product.category = brief["category"]
    product.selling_points_json = json_dumps(brief["selling_points"])
    product.audience = brief["audience"]
    product.price = brief["price"]
    product.tone = brief["tone"]
    project.status = "analyzed"
    session.commit()
    session.refresh(product)
    return product


def update_product(session: Session, product: Product, payload) -> Product:
    brief = get_brief(product)
    data = payload.model_dump(exclude_none=True)
    if "selling_points" in data:
        brief["selling_points"] = [truncate(str(p), 30) for p in data["selling_points"] if str(p).strip()][:5]
        product.selling_points_json = json_dumps(brief["selling_points"])
    for key in ("title", "category", "audience", "price", "tone"):
        if key in data:
            brief[key] = str(data[key])
            setattr(product, key, str(data[key]))
    product.brief_json = json_dumps(brief)
    session.commit()
    session.refresh(product)
    return product
