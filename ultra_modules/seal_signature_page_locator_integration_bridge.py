import json
import logging
from typing import Dict, Any, List, Optional
from ultra_modules.seal_signature_page_locator_core_engine import generate_seal_signature_page_list

logger = logging.getLogger(__name__)

class SchemaConverter:
    @staticmethod
    def normalize_key(key: Any) -> str:
        if not isinstance(key, str):
            key = str(key)
        return key.lower().replace("_", "").replace("-", "").replace(" ", "")

    @classmethod
    def silent_map_and_convert(cls, data: Any, audit_trace: List[str]) -> Dict[str, Any]:
        if data is None:
            audit_trace.append("Root data is None. Defaulting to empty dict.")
            return {}
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    audit_trace.append("Root data was a string. Successfully parsed as JSON dict.")
                    data = parsed
                else:
                    audit_trace.append("Root data was a JSON string but not a dict. Wrapping in dict.")
                    return {"_raw_value": parsed}
            except Exception:
                audit_trace.append("Root data was an invalid JSON string. Wrapping in dict.")
                return {"_raw_text": data}

        if not isinstance(data, dict):
            audit_trace.append(f"Root data was not a dict ({type(data).__name__}). Wrapping in dict.")
            return {"_raw_data": data}

        result = {}
        for k, v in data.items():
            norm_k = cls.normalize_key(k)
            if isinstance(v, dict):
                result[norm_k] = cls.silent_map_and_convert(v, audit_trace)
            elif isinstance(v, list):
                new_list = []
                for item in v:
                    if isinstance(item, dict):
                        new_list.append(cls.silent_map_and_convert(item, audit_trace))
                    else:
                        new_list.append(item)
                result[norm_k] = new_list
            else:
                result[norm_k] = v

            if norm_k != k:
                 # only trace root level normalizations to avoid spam
                 audit_trace.append(f"Normalized key '{k}' to '{norm_k}'")
        return result


class SealSignaturePageLocatorIntegrationBridge:
    """
    千页标书全文需盖公章/法人章/骑缝章/签字页码一键精准定位清单生成器
    跨系统数据契约无缝桥接与Schema版本自动向前兼容转换器
    """

    @staticmethod
    def process_request(request_payload: Any) -> Dict[str, Any]:
        """
        自动识别新旧版本字段差异并静默映射转换，提取参数并调用核心引擎。
        包含完整的故障容忍和缺省值回退。
        """
        audit_trace: List[str] = []
        audit_trace.append("Starting SealSignaturePageLocatorIntegrationBridge.process_request")

        # 1. 结构清洗和Key归一化
        clean_data = SchemaConverter.silent_map_and_convert(request_payload, audit_trace)

        # 2. 参数提取与自动映射 (新旧版兼容)
        total_pages = None
        document_content_list = None
        seal_keywords = None
        signature_keywords = None
        cross_page_seal_interval = None

        # 提取 total_pages (支持 'total_pages', 'totalpages', 'page_count', 'pagecount')
        for key in ["totalpages", "pagecount"]:
            if key in clean_data:
                val = clean_data[key]
                try:
                    if val is not None:
                        total_pages = int(val)
                        audit_trace.append(f"Extracted total_pages from '{key}': {total_pages}")
                        break
                except (ValueError, TypeError):
                    audit_trace.append(f"Failed to cast '{key}' to int: {val}")

        # 提取 document_content_list (支持 'document_content_list', 'documentcontentlist', 'pages', 'pagetexts')
        for key in ["documentcontentlist", "pages", "pagetexts"]:
            if key in clean_data:
                val = clean_data[key]
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                        audit_trace.append(f"Parsed JSON string for '{key}'")
                    except Exception:
                        audit_trace.append(f"Failed to parse JSON string for '{key}', wrapping as single page")
                        val = [val]

                if isinstance(val, list):
                    document_content_list = [str(item) for item in val]
                    audit_trace.append(f"Extracted document_content_list from '{key}', length: {len(document_content_list)}")
                    break
                else:
                    audit_trace.append(f"'{key}' is not a list, converting to single element list")
                    document_content_list = [str(val)]
                    break

        # 提取 seal_keywords (支持 'seal_keywords', 'sealkeywords', 'seals')
        for key in ["sealkeywords", "seals"]:
            if key in clean_data:
                val = clean_data[key]
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                        audit_trace.append(f"Parsed JSON string for '{key}'")
                    except Exception:
                         # comma separated?
                         if "," in val:
                             val = [k.strip() for k in val.split(",")]
                             audit_trace.append(f"Parsed comma separated string for '{key}'")
                         else:
                             val = [val]
                             audit_trace.append(f"Wrapped string for '{key}' in list")
                if isinstance(val, list):
                    seal_keywords = [str(k) for k in val]
                    audit_trace.append(f"Extracted seal_keywords from '{key}'")
                    break
                else:
                    seal_keywords = [str(val)]
                    audit_trace.append(f"Wrapped string for '{key}' in list")
                    break

        # 提取 signature_keywords (支持 'signature_keywords', 'signaturekeywords', 'signatures')
        for key in ["signaturekeywords", "signatures"]:
            if key in clean_data:
                val = clean_data[key]
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                        audit_trace.append(f"Parsed JSON string for '{key}'")
                    except Exception:
                         if "," in val:
                             val = [k.strip() for k in val.split(",")]
                             audit_trace.append(f"Parsed comma separated string for '{key}'")
                         else:
                             val = [val]
                             audit_trace.append(f"Wrapped string for '{key}' in list")
                if isinstance(val, list):
                    signature_keywords = [str(k) for k in val]
                    audit_trace.append(f"Extracted signature_keywords from '{key}'")
                    break
                else:
                    signature_keywords = [str(val)]
                    audit_trace.append(f"Wrapped string for '{key}' in list")
                    break

        # 提取 cross_page_seal_interval (支持 'cross_page_seal_interval', 'crosspagesealinterval', 'interval')
        for key in ["crosspagesealinterval", "interval"]:
            if key in clean_data:
                val = clean_data[key]
                try:
                    if val is not None:
                        cross_page_seal_interval = int(val)
                        audit_trace.append(f"Extracted cross_page_seal_interval from '{key}': {cross_page_seal_interval}")
                        break
                except (ValueError, TypeError):
                    audit_trace.append(f"Failed to cast '{key}' to int: {val}")

        # 3. 调用核心引擎
        try:
            core_result = generate_seal_signature_page_list(
                total_pages=total_pages,
                document_content_list=document_content_list,
                seal_keywords=seal_keywords,
                signature_keywords=signature_keywords,
                cross_page_seal_interval=cross_page_seal_interval
            )
            audit_trace.append("Core engine executed successfully")
        except Exception as e:
            audit_trace.append(f"Core engine failed with error: {str(e)}")
            # 严重失败兜底
            core_result = {
                "total_pages": total_pages or 100,
                "seal_pages": [],
                "signature_pages": [],
                "cross_page_seals": [],
                "config_used": {},
                "error": str(e)
            }

        # 4. 组装最终结果
        bridge_result = {
            "status": "success" if "error" not in core_result else "error",
            "data": core_result,
            "_audit_trace": audit_trace
        }

        return bridge_result
