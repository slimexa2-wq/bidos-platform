import json
import argparse
import sys
from typing import Dict, Any, Optional

def analyze_bid_document(document: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pure function to analyze a bid document and return a decision card.
    Follows "Complex Backend, Minimalist Frontend": auto-fallback, default values,
    dirty data self-healing, zero configuration.
    """
    # Self-healing & default fallback for missing or None document
    if document is None:
        document = {}
    elif not isinstance(document, dict):
        # Self-heal if a string is passed unexpectedly
        try:
            document = json.loads(str(document))
        except (ValueError, TypeError):
            document = {}

    # Extract fields with safe defaults
    project_name = document.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        project_name = "未命名项目(系统自动兜底)"
    else:
        project_name = project_name.strip()

    budget = document.get("budget")
    try:
        budget_val = float(budget) if budget is not None else 0.0
    except (ValueError, TypeError):
        budget_val = 0.0

    deadline = document.get("deadline")
    if not isinstance(deadline, str) or not deadline.strip():
        deadline = "1970-01-01(默认未知)"
    else:
        deadline = deadline.strip()

    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        requirements = ["无明确要求(自动填充)"]
    else:
        requirements = [str(req).strip() for req in requirements if req]
        if not requirements:
            requirements = ["无明确要求(自动填充)"]

    # Business Logic for Go/No-Go Decision
    # Extremely simplified example logic for demonstration:
    # Go: budget > 500000 and deadline not 1970
    # No-Go: budget < 50000 (too small) or has "黑名单" in requirements
    # Caution: anything else

    recommendation = "慎投"
    reasons = []

    if any("黑名单" in req for req in requirements):
        recommendation = "禁投"
        reasons.append("要求中包含禁投关键字(如黑名单)")
    elif budget_val < 50000:
        recommendation = "禁投"
        reasons.append(f"预算太低: {budget_val}")
    elif budget_val > 500000 and "1970" not in deadline:
        recommendation = "可投"
        reasons.append(f"预算充足 ({budget_val}) 且时间明确")
    else:
        reasons.append("常规项目，需进一步评估细节")

    return {
        "project_name": project_name,
        "budget": budget_val,
        "deadline": deadline,
        "requirements": requirements,
        "recommendation": recommendation,
        "reasons": reasons
    }

def format_as_excel_text(card: Dict[str, Any]) -> str:
    """
    Format the decision card as a mock Excel-like text table.
    """
    lines = []
    lines.append("=" * 50)
    lines.append(f"| {'一页式极简投标决策卡':^46} |")
    lines.append("=" * 50)
    lines.append(f"| 项目名称 | {card['project_name']:<35} |")
    lines.append(f"| 预算金额 | {card['budget']:<35.2f} |")
    lines.append(f"| 截止日期 | {card['deadline']:<35} |")
    lines.append(f"| 核心要求 | {', '.join(card['requirements']):<35} |")
    lines.append("-" * 50)
    lines.append(f"| 建议结果 | {card['recommendation']:<35} |")
    lines.append(f"| 判定理由 | {'; '.join(card['reasons']):<35} |")
    lines.append("=" * 50)
    return "\n".join(lines)


def main():
    """
    CLI Entrypoint: Zero-parameter one-click CLI adapter.
    """
    parser = argparse.ArgumentParser(description="一键生成投标决策卡")
    parser.add_argument("--doc", type=str, default="{}", help="JSON 格式的标书数据")
    parser.add_argument("--format", type=str, choices=["json", "excel"], default="excel", help="输出格式")

    args = parser.parse_args()

    try:
        doc = json.loads(args.doc)
    except json.JSONDecodeError:
        print("错误: 传入的标书数据不是有效的 JSON 字符串，采用空数据兜底。")
        doc = {}

    card = analyze_bid_document(doc)

    if args.format == "json":
        print(json.dumps(card, ensure_ascii=False, indent=2))
    else:
        print(format_as_excel_text(card))

if __name__ == "__main__":
    main()
