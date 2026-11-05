"""商务标证明材料完备性自动核对器。

用于防止因为漏放一张社保证明或合同关键页而废标。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

class CommercialMaterialChecker:
    def __init__(self, required_materials: Optional[List[str]] = None):
        if required_materials is None:
            self.required_materials = [
                "社保证明",
                "合同关键页"
            ]
        else:
            self.required_materials = required_materials

    def check(self, provided_materials: List[str]) -> Dict[str, Any]:
        """核对提供的材料是否包含所有必需的商务标证明材料。

        参数
        ----
        provided_materials : List[str]
            已提供的材料名称列表。

        返回
        ----
        Dict[str, Any]
            包含检查结果的字典：
            - is_complete: bool，是否完备
            - missing_materials: List[str]，缺失的材料列表
        """
        missing_materials = [
            material for material in self.required_materials
            if material not in provided_materials
        ]

        return {
            "is_complete": len(missing_materials) == 0,
            "missing_materials": missing_materials
        }
