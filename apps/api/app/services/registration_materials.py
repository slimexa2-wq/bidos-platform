"""报名资料体系（验收清单 §二「报名管理」3 项 ❌ 缺口收尾）。

一句话职责
----------

围绕"报名材料清单"补齐三项能力，全部**确定性规则，无 LLM**：

1. **材料缺项自动识别**（``identify_required_materials``）：根据招标公告文本
   / 结构化字段，识别该项目报名需要哪些材料（营业执照、许可证、业绩、
   人员证书、保证金凭证等），并对照公司资料库标注 ready / missing / confirm。
2. **公司资料库调用**（``CompanyProfileLibrary``）：扫描公司资质目录
   （只读），建立"资料名称 → 文件"索引，供材料匹配与打包。
3. **报名材料包生成**（``generate_material_package``）：把 ready 材料的
   文件引用组装成一个清单（manifest），**不复制、不改动原文件**——
   原文件仍是真相源，清单里保存路径 + 校验哈希，供人工核对后正式使用。

反幻觉硬约束（交接文档 §1.3 / §3.3）
------------------------------------

* 关键词→材料类型的映射是**可配置规则表**，不是 LLM 猜测；
* 识别不出的要求进 ``unknown_requirements``，绝不硬套材料类型；
* 公司资料库文件**只读**（open 为 "rb" 计算哈希，绝不写入）；
* 材料包只是 **manifest（清单）**，不打包复制文件——正式使用前必须人工确认
  （交接文档 §2.3：生成报名材料包必须人工确认后正式使用）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "MATERIAL_RULES",
    "CompanyProfileLibrary",
    "identify_required_materials",
    "generate_material_package",
]

# ---------------------------------------------------------------------------
# 规则表：招标公告常见表述 → 材料类型。可按业务需要在 yaml 化时外置。
# 每条：材料类型 → (资料库关键词列表, 公告关键词正则)
# ---------------------------------------------------------------------------

MATERIAL_RULES: list[dict[str, Any]] = [
    {
        "type": "business_license",
        "label": "营业执照",
        "library_keywords": ["营业执照", "统一社会信用", "三证合一"],
        "notice_pattern": r"营业执[照照]|统一社会信用代码",
    },
    {
        "type": "labor_dispatch_license",
        "label": "劳务派遣经营许可证",
        "library_keywords": ["劳务派遣", "派遣经营许可"],
        "notice_pattern": r"劳务派遣经营许可|劳务派遣业务许可",
    },
    {
        "type": "legal_representative",
        "label": "法人身份证明",
        "library_keywords": ["法人身份", "法定代表人", "法人证书"],
        "notice_pattern": r"法定代表[人人]|法人身份|授权委托书",
    },
    {
        "type": "similar_performance",
        "label": "近三年类似业绩证明",
        "library_keywords": ["业绩", "合同", "中标通知书"],
        "notice_pattern": r"(?:近|最近)?[三3]年.{0,6}(?:类似|同类)?业绩|类似项目业绩|业绩证明",
    },
    {
        "type": "personnel_certificates",
        "label": "项目负责人及人员证书",
        "library_keywords": ["人员证书", "项目经理", "职称证", "上岗证"],
        "notice_pattern": r"项目负责[人人]|人员证[书件]|职称证|上岗证|资格证书",
    },
    {
        "type": "bid_bond_voucher",
        "label": "保证金缴纳凭证",
        "library_keywords": ["保证金", "缴纳凭证", "回单"],
        "notice_pattern": r"投标保证金|保证金缴[纳纳]",
    },
    {
        "type": "financial_report",
        "label": "财务状况报告",
        "library_keywords": ["财务报告", "审计报告", "财务报表"],
        "notice_pattern": r"财务状况|审计报告|财务报表",
    },
    {
        "type": "no_violation_statement",
        "label": "无违法违规声明",
        "library_keywords": ["无违法", "信用中国", "声明函"],
        "notice_pattern": r"无违法(?:违纪)?(?:行为)?(?:记录)?声明|信用中国|失信被执行",
    },
]


def _hash_file(path: Path) -> str:
    """只读计算文件 SHA-256（绝不修改原文件）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 公司资料库（只读扫描）
# ---------------------------------------------------------------------------


class CompanyProfileLibrary:
    """公司资质/资料库：扫描目录建立「资料名 → 文件」索引。

    目录结构约定（与祥能现有归档习惯兼容）：
    ``<资料根目录>/<资料名称相关文件名>.pdf/docx/...``
    文件名包含资料关键词即视为该类资料的候选（如「营业执照-祥能-2026.pdf」）。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._index: dict[str, list[dict[str, Any]]] | None = None

    def build_index(self) -> dict[str, list[dict[str, Any]]]:
        """扫描目录，按 MATERIAL_RULES 的 library_keywords 归类。"""
        index: dict[str, list[dict[str, Any]]] = {}
        if self.root.exists():
            for path in sorted(self.root.rglob("*")):
                if not path.is_file() or path.name.startswith("~$"):
                    continue
                for rule in MATERIAL_RULES:
                    if any(kw in path.name for kw in rule["library_keywords"]):
                        index.setdefault(rule["type"], []).append(
                            {
                                "path": str(path),
                                "file_name": path.name,
                                "size_bytes": path.stat().st_size,
                                "sha256": _hash_file(path),
                            }
                        )
        self._index = index
        return index

    def find(self, material_type: str) -> list[dict[str, Any]]:
        """取某材料类型的候选文件；索引未建时自动建。"""
        if self._index is None:
            self.build_index()
        return list((self._index or {}).get(material_type, []))

    @property
    def indexed(self) -> bool:
        return self._index is not None


# ---------------------------------------------------------------------------
# 1. 材料缺项自动识别
# ---------------------------------------------------------------------------


def identify_required_materials(
    *,
    announcement_text: str = "",
    announcement_fields: dict[str, Any] | None = None,
    library: CompanyProfileLibrary | None = None,
    bid_amount: float | None = None,
) -> dict[str, Any]:
    """根据招标公告识别报名所需材料，并对照资料库标注状态。

    参数
    ----
    announcement_text : 公告正文/摘要文本（可为空，退化为通用清单）
    announcement_fields : 结构化字段（如 {"保证金": 50000}，可辅助判断）
    library : 公司资料库；为空则材料状态全部为 ``missing``（诚实：没库就没法判定 ready）
    bid_amount : 保证金金额（元）。>0 时保证金凭证必查；0/None 时该材料标 ``not_required``

    返回结构与前端 materials 完全兼容：
    ``{"materials": [{"id","type","label","status","source","files"?}], "unknown_requirements": [...]}``
    状态：``ready``（资料库有候选）/ ``missing``（公告要求但库没有）/
    ``confirm``（公告未明确要求但通用清单建议准备）/ ``not_required``（不适用）
    """
    announcement_fields = announcement_fields or {}
    text = str(announcement_text or "")
    materials: list[dict[str, Any]] = []
    unknown: list[str] = []

    for rule in MATERIAL_RULES:
        required_by_notice = bool(re.search(rule["notice_pattern"], text))
        # 保证金凭证：金额>0 即要求；金额明确为 0/未要求则 not_required
        if rule["type"] == "bid_bond_voucher" and bid_amount is not None:
            required_by_notice = float(bid_amount) > 0

        candidates = library.find(rule["type"]) if library else []
        if required_by_notice:
            status = "ready" if candidates else "missing"
        elif candidates:
            # 公告没提，但资料库有现成的 → 建议准备，交人工确认
            status = "confirm"
        else:
            # 公告没提，库也没有 → 不适用（不生成噪音）
            if not text:
                # 无公告文本时退化为通用清单：主要材料给 confirm 供人工勾选
                status = "confirm"
            elif rule["type"] == "bid_bond_voucher" and bid_amount is not None:
                # 金额明确为 0 → 明确标 not_required（比"消失"更诚实）
                status = "not_required"
            else:
                continue

        item: dict[str, Any] = {
            "id": f"mat_{rule['type']}",
            "type": rule["type"],
            "label": rule["label"],
            "status": status,
            "source": "公司资质库" if candidates else ("招标公告要求" if required_by_notice else "通用清单"),
        }
        if candidates:
            item["files"] = candidates
        materials.append(item)

    # 识别不出的要求：把公告里带「提供/提交/附」但没命中任何规则的句子捞出来
    for sentence in re.split(r"[。；;\n]", text):
        if re.search(r"(提供|提交|附上|出具)", sentence) and not any(
            re.search(rule["notice_pattern"], sentence) for rule in MATERIAL_RULES
        ):
            cleaned = sentence.strip()
            if cleaned and cleaned not in unknown:
                unknown.append(cleaned)

    return {
        "materials": materials,
        "unknown_requirements": unknown[:20],
        "library_scanned": bool(library and library.indexed),
        "notices": (
            []
            if library
            else ["未提供公司资料库路径，所有材料状态为 missing/confirm，无法判定 ready。"]
        ),
    }


# ---------------------------------------------------------------------------
# 3. 报名材料包生成（manifest，不复制文件）
# ---------------------------------------------------------------------------


def generate_material_package(
    materials: Iterable[dict[str, Any]],
    *,
    project_id: str,
    project_name: str = "",
) -> dict[str, Any]:
    """把 ready 材料的文件引用组装成材料包清单（manifest）。

    红线：**不复制、不移动、不修改任何原文件**；清单保存路径 + SHA-256，
    人工核对后正式使用。missing 材料进 ``missing_list``，绝不伪装成已齐。
    """
    files: list[dict[str, Any]] = []
    missing_list: list[str] = []
    confirm_list: list[str] = []

    for material in materials:
        label = str(material.get("label", ""))
        status = str(material.get("status", ""))
        if status == "ready":
            for file_info in material.get("files", []) or []:
                files.append(
                    {
                        "material": label,
                        "file_name": file_info["file_name"],
                        "path": file_info["path"],
                        "sha256": file_info.get("sha256"),
                    }
                )
        elif status == "missing":
            missing_list.append(label)
        elif status == "confirm":
            confirm_list.append(label)

    manifest = {
        "project_id": project_id,
        "project_name": project_name,
        "generated_note": "本清单由 BidOS 生成，仅为文件引用清单；正式使用前必须人工核对原件。",
        "files": files,
        "file_count": len(files),
        "missing_list": missing_list,
        "confirm_list": confirm_list,
        "complete": not missing_list,
    }
    # manifest 自身哈希：防止生成后被静默篡改
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str)
    manifest["manifest_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return manifest

