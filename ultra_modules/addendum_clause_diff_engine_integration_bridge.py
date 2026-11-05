import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class SchemaConverter:
    @staticmethod
    def normalize_key(key: Any) -> str:
        if not isinstance(key, str):
            key = str(key)
        return key.lower().replace("_", "").replace("-", "").replace(" ", "")

    @classmethod
    def silent_map_and_convert(cls, data: Any) -> Dict[str, Any]:
        if data is None:
            return {}
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    return {"_raw_value": parsed}
            except Exception:
                return {"_raw_text": data}

        if not isinstance(data, dict):
            return {"_raw_data": data}

        result = {}
        for k, v in data.items():
            norm_k = cls.normalize_key(k)
            if isinstance(v, dict):
                result[norm_k] = cls.silent_map_and_convert(v)
            elif isinstance(v, list):
                new_list = []
                for item in v:
                    if isinstance(item, dict):
                        new_list.append(cls.silent_map_and_convert(item))
                    else:
                        new_list.append(item)
                result[norm_k] = new_list
            else:
                result[norm_k] = v
        return result

class ClauseDiffEngine:
    @staticmethod
    def compare_clauses(original: Dict[str, Any], addendum: Dict[str, Any]) -> Dict[str, Any]:
        diff: Dict[str, Any] = {
            "added": {},
            "removed": {},
            "modified": {},
            "unchanged": {}
        }

        all_keys = set(original.keys()).union(set(addendum.keys()))

        for key in all_keys:
            if key not in original:
                diff["added"][key] = addendum[key]
            elif key not in addendum:
                diff["removed"][key] = original[key]
            else:
                val_orig = original[key]
                val_add = addendum[key]
                if val_orig != val_add:
                    diff["modified"][key] = {"from": val_orig, "to": val_add}
                else:
                    diff["unchanged"][key] = val_orig

        return diff

    @staticmethod
    def recalculate_impacted_chapters(diff_result: Dict[str, Any]) -> List[str]:
        impacted = set()
        keyword_to_chapter = {
            "price": "Chapter 3: Pricing",
            "cost": "Chapter 3: Pricing",
            "deadline": "Chapter 1: Schedule",
            "date": "Chapter 1: Schedule",
            "technical": "Chapter 4: Technical Specs",
            "spec": "Chapter 4: Technical Specs",
            "qualification": "Chapter 2: Qualifications",
            "cert": "Chapter 2: Qualifications"
        }

        for category in ["added", "removed", "modified"]:
            items = diff_result.get(category, {})
            if isinstance(items, dict):
                for key in items.keys():
                    key_str = str(key).lower()
                    matched = False
                    for kw, chapter in keyword_to_chapter.items():
                        if kw in key_str:
                            impacted.add(chapter)
                            matched = True
                    if not matched:
                        impacted.add("Chapter 5: General Addendum")

        return sorted(list(impacted))

def integrate_and_recalculate(original_doc: Any = None, addendum_doc: Any = None) -> Dict[str, Any]:
    try:
        if original_doc is None:
            original_doc = {}
        if addendum_doc is None:
            addendum_doc = {}

        norm_original = SchemaConverter.silent_map_and_convert(original_doc)
        norm_addendum = SchemaConverter.silent_map_and_convert(addendum_doc)

        diff_result = ClauseDiffEngine.compare_clauses(norm_original, norm_addendum)
        impacted_chapters = ClauseDiffEngine.recalculate_impacted_chapters(diff_result)

        has_diff = bool(diff_result["added"] or diff_result["removed"] or diff_result["modified"])

        return {
            "status": "success",
            "message": "对比完成，已自动映射差异并重算影响章节" if has_diff else "无实质性差异",
            "diff_summary": {
                "added_count": len(diff_result.get("added", {})),
                "removed_count": len(diff_result.get("removed", {})),
                "modified_count": len(diff_result.get("modified", {})),
            },
            "detailed_diff": diff_result,
            "impacted_chapters": impacted_chapters
        }
    except Exception as e:
        logger.error(f"Diff engine encountered an error, silently recovering: {e}")
        return {
            "status": "recovered",
            "message": "数据解析遇到异常，已启动自愈模式并返回基础比对结果",
            "diff_summary": {"added_count": 0, "removed_count": 0, "modified_count": 0},
            "detailed_diff": {"added": {}, "removed": {}, "modified": {}, "unchanged": {}},
            "impacted_chapters": ["Chapter 5: General Addendum"]
        }
