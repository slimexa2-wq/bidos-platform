import math
from typing import List, Dict, Any, Optional

def generate_seal_signature_page_list(
    total_pages: Optional[int] = None,
    document_content_list: Optional[List[str]] = None,
    seal_keywords: Optional[List[str]] = None,
    signature_keywords: Optional[List[str]] = None,
    cross_page_seal_interval: Optional[int] = None,
    default_total_pages: int = 100,
) -> Dict[str, Any]:
    """
    千页标书全文需盖公章/法人章/骑缝章/签字页码一键精准定位清单生成器

    :param total_pages: 标书总页数。若为None，将尝试从document_content_list推断，否则使用default_total_pages。
    :param document_content_list: 每页的文本内容列表。
    :param seal_keywords: 需要盖章的关键字列表。默认为 ['公章', '法人章', '盖章', '签章']。
    :param signature_keywords: 需要签字的关键字列表。默认为 ['签字', '签名', '法定代表人签字', '授权代表签字']。
    :param cross_page_seal_interval: 骑缝章的间隔页数。默认为 10。
    :param default_total_pages: 默认总页数（兜底用）。
    :return: 包含各项页码定位结果的字典。
    """

    # --- 1. 脏数据静默清洗与缺参赋默认值 ---

    if document_content_list is None:
        document_content_list = []

    # 确保 total_pages 是一个合理的正整数
    if total_pages is None or not isinstance(total_pages, int) or total_pages <= 0:
        if document_content_list:
            total_pages = len(document_content_list)
        else:
            total_pages = default_total_pages

    # 如果给定的内容列表超过总页数，截断；不足则用空字符串补齐（静默容错）
    # 创建新列表以保证纯函数的无副作用特性
    if len(document_content_list) > total_pages:
        document_content_list = document_content_list[:total_pages]
    else:
        document_content_list = document_content_list + [''] * (total_pages - len(document_content_list))

    if seal_keywords is None:
        seal_keywords = ['公章', '法人章', '盖章', '签章']
    if not isinstance(seal_keywords, list):
        seal_keywords = [str(seal_keywords)]

    if signature_keywords is None:
        signature_keywords = ['签字', '签名', '法定代表人签字', '授权代表签字']
    if not isinstance(signature_keywords, list):
        signature_keywords = [str(signature_keywords)]

    if cross_page_seal_interval is None or not isinstance(cross_page_seal_interval, int) or cross_page_seal_interval <= 0:
        cross_page_seal_interval = 10

    # --- 2. 核心定位算法 ---

    seal_pages = set()
    signature_pages = set()

    # 全文扫描匹配关键字
    for i, page_text in enumerate(document_content_list):
        page_num = i + 1 # 页码从 1 开始

        # 确保是字符串
        if not isinstance(page_text, str):
            page_text = str(page_text) if page_text is not None else ""

        for keyword in seal_keywords:
            if keyword in page_text:
                seal_pages.add(page_num)
                break # 匹配到一个关键字即可

        for keyword in signature_keywords:
            if keyword in page_text:
                signature_pages.add(page_num)
                break

    # 始终包含最后一页作为盖章/签字的默认兜底页 (很多标书要求最后一页盖章签字)
    if total_pages > 0:
        seal_pages.add(total_pages)
        signature_pages.add(total_pages)

    # 骑缝章计算
    # 假设骑缝章盖在侧边，每间隔 cross_page_seal_interval 页盖一次，每次盖满该区间的页面边缘
    # 这里返回的是需要盖骑缝章的起始-结束页码段
    cross_page_seals = []
    if total_pages > 0:
        num_seals = math.ceil(total_pages / cross_page_seal_interval)
        for i in range(num_seals):
            start_page = i * cross_page_seal_interval + 1
            end_page = min((i + 1) * cross_page_seal_interval, total_pages)
            cross_page_seals.append(f"{start_page}-{end_page}")

    # --- 3. 结果组装 ---

    result = {
        "total_pages": total_pages,
        "seal_pages": sorted(list(seal_pages)),
        "signature_pages": sorted(list(signature_pages)),
        "cross_page_seals": cross_page_seals,
        "config_used": {
            "seal_keywords": seal_keywords,
            "signature_keywords": signature_keywords,
            "cross_page_seal_interval": cross_page_seal_interval
        }
    }

    return result
