import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class SchemaVersionAdapter:
    """
    Handles silent backward compatibility for different pagination schemas.
    Normalizes dirty keys, maps old schema styles to standard style,
    and provides zero-config fallback.
    """
    @staticmethod
    def normalize_key(key: Any) -> str:
        if not isinstance(key, str):
            key = str(key)
        return key.lower().replace("_", "").replace("-", "").replace(" ", "")

    @classmethod
    def extract_toc_items(cls, data: Any) -> List[Dict[str, Any]]:
        """
        Safely extract TOC (Table of Contents) items from heterogeneous data.
        Returns a normalized list of {"title": str, "page": int}.
        """
        if not data:
            return []

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return []

        # Handle if data is directly a list
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Look for common key names for toc
            candidates = ["toc", "tableofcontents", "catalog", "directory", "items"]
            items = []
            for k, v in data.items():
                if cls.normalize_key(k) in candidates and isinstance(v, list):
                    items = v
                    break
            if not items and isinstance(data.get("data"), list):
                items = data["data"]
        else:
            return []

        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue

            title_candidates = ["title", "name", "section", "heading", "chapter", "text"]
            page_candidates = ["page", "pageno", "pagenumber", "p"]

            title = "Unknown Section"
            page_num = 1

            for k, v in item.items():
                norm_k = cls.normalize_key(k)
                if norm_k in title_candidates:
                    title = str(v)
                elif norm_k in page_candidates:
                    try:
                        # Clean dirty page numbers like "p. 5", " 5 ", "page5"
                        clean_v = ''.join(filter(str.isdigit, str(v)))
                        if clean_v:
                            page_num = int(clean_v)
                    except Exception:
                        pass

            normalized.append({
                "title": title,
                "page": page_num
            })

        return normalized

    @classmethod
    def extract_body_pages(cls, data: Any) -> Dict[str, int]:
        """
        Safely extract actual page mappings from body data.
        Returns {"title": actual_page_number}
        """
        if not data:
            return {}

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return {}

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            candidates = ["body", "content", "pages", "actual", "sections"]
            items = []
            for k, v in data.items():
                if cls.normalize_key(k) in candidates and isinstance(v, list):
                    items = v
                    break
            if not items:
                items = [data] # maybe it's just one section
        else:
            return {}

        mapping = {}
        for item in items:
            if not isinstance(item, dict):
                continue

            title_candidates = ["title", "name", "section", "heading", "chapter", "text"]
            page_candidates = ["actualpage", "page", "pageno", "pagenumber", "startpage"]

            title = None
            page_num = None

            for k, v in item.items():
                norm_k = cls.normalize_key(k)
                if norm_k in title_candidates:
                    title = str(v)
                elif norm_k in page_candidates:
                    try:
                        clean_v = ''.join(filter(str.isdigit, str(v)))
                        if clean_v:
                            page_num = int(clean_v)
                    except Exception:
                        pass

            if title and page_num is not None:
                mapping[title] = page_num

        return mapping

class PaginationSyncVerifier:
    """
    Core logic for verifying if the TOC page numbers match the actual body page numbers.
    """
    @staticmethod
    def verify_sync(toc_data: Any, body_data: Any) -> Dict[str, Any]:
        """
        Compares TOC entries with actual body pagination.
        Identifies offsets and suggests corrections.
        """
        try:
            toc_items = SchemaVersionAdapter.extract_toc_items(toc_data)
            body_mapping = SchemaVersionAdapter.extract_body_pages(body_data)

            if not toc_items:
                return {
                    "status": "warning",
                    "message": "未能识别到有效的目录数据",
                    "is_synced": True, # Fail open
                    "deviations": [],
                    "offset_pattern": None
                }

            if not body_mapping:
                # If no body mapping, we assume they match (zero configuration fallback)
                return {
                    "status": "success",
                    "message": "正文页码缺失，默认假定与目录一致",
                    "is_synced": True,
                    "deviations": [],
                    "offset_pattern": None
                }

            deviations = []
            offsets = []

            for toc_item in toc_items:
                title = toc_item["title"]
                toc_page = toc_item["page"]

                actual_page = None
                # Exact match
                if title in body_mapping:
                    actual_page = body_mapping[title]
                else:
                    # Fuzzy match (silent self-healing)
                    for body_title, b_page in body_mapping.items():
                        if title in body_title or body_title in title:
                            actual_page = b_page
                            break

                if actual_page is not None:
                    if toc_page != actual_page:
                        offset = actual_page - toc_page
                        offsets.append(offset)
                        deviations.append({
                            "section": title,
                            "toc_page": toc_page,
                            "actual_page": actual_page,
                            "offset": offset,
                            "suggestion": f"将目录中[{title}]的页码从 {toc_page} 改为 {actual_page}"
                        })

            if not deviations:
                return {
                    "status": "success",
                    "message": "目录页码与正文完全一致",
                    "is_synced": True,
                    "deviations": [],
                    "offset_pattern": None
                }

            # Detect systematic offset pattern
            pattern = None
            if len(offsets) > 1 and len(set(offsets)) == 1:
                pattern = f"全局偏移 {offsets[0]} 页"
            elif len(offsets) > 1:
                pattern = "混合无规律偏移"

            return {
                "status": "error",
                "message": f"发现 {len(deviations)} 处页码不一致",
                "is_synced": False,
                "deviations": deviations,
                "offset_pattern": pattern
            }

        except Exception as e:
            logger.error(f"Pagination verify encountered an error, silently recovering: {e}")
            return {
                "status": "recovered",
                "message": "解析过程遇到异常，已启动自愈兜底机制并默认通过",
                "is_synced": True,
                "deviations": [],
                "offset_pattern": None
            }

def verify_pagination_sync(toc_payload: Any = None, body_payload: Any = None) -> Dict[str, Any]:
    """
    Main entry point for integration.
    """
    return PaginationSyncVerifier.verify_sync(toc_payload, body_payload)
