# -*- coding: utf-8 -*-
"""
营业部 → 游资近似映射（P2-5）。

基于公开、广为人知的游资席位（券商营业部名称）做**子串匹配**的近似"认人"，
用于对龙虎榜机构席位明细（Tushare ``top_inst`` 的 ``exalter`` 字段）做标注，
免上 Tushare 10000 档付费游资识别接口。

⚠️ 这是**近似**映射：
- 仅覆盖部分知名游资席位，命中靠营业部名关键词子串匹配，可能漏报/误报；
- 营业部更名、游资换席位会导致映射过时，需人工维护；
- 仅作辅助参考，不作为交易决策依据。
"""

from typing import Any, Dict, List, Optional

# 关键词（营业部名子串）→ 游资标签。按公开龙虎榜常见知名席位整理，保守取值。
# 命中规则：exalter 包含 key 子串即归类为对应游资。多个命中取第一个匹配。
HOT_MONEY_SEATS: Dict[str, str] = {
    "拉萨": "拉萨天团（东财系）",
    "深圳益田路荣超商务中心": "深圳益田路（一线游资）",
    "上海溧阳路": "上海溧阳路",
    "上海江苏路": "上海江苏路",
    "杭州上塘路": "杭州上塘路",
    "宁波桑田路": "宁波桑田路（敢死队）",
    "宁波解放南路": "宁波解放南路",
    "绍兴": "绍兴帮",
    "深圳蛇口工业七路": "深圳蛇口",
    "成都南一环路": "成都帮",
    "北京西三环中路": "北京西三环",
    "佛山季华六路": "佛山系",
}


def classify_seat(exalter: Optional[str]) -> Optional[str]:
    """根据营业部名称返回游资标签；未命中或空返回 None。"""
    if not exalter:
        return None
    name = str(exalter)
    for keyword, label in HOT_MONEY_SEATS.items():
        if keyword in name:
            return label
    return None


def annotate_seats(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为席位明细列表逐条追加 ``hot_money`` 字段（游资标签或 None）。

    原字段不变；记录用 ``exalter`` 键作为营业部名（缺失则视为未知）。
    """
    annotated: List[Dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["hot_money"] = classify_seat(record.get("exalter"))
        annotated.append(item)
    return annotated
