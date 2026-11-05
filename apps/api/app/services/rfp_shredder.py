from __future__ import annotations

import csv
import io
import json
import re
from enum import Enum
from typing import Any, List

from pydantic import BaseModel, Field

from app.ai.base import AIProvider
from app.domain import SourcePointer
from app.documents.base import ParsedChunk


class ClauseType(str, Enum):
    DISQUALIFICATION = "废标项"
    SCORING = "评分点"
    UNKNOWN = "兜底"


class Clause(BaseModel):
    id: str
    clause_type: ClauseType
    content: str
    source_pointer: SourcePointer
    confidence: float = Field(default=0.5, ge=0, le=1)
    needs_human_review: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RFPShredder:
    """双通道废标项粉碎机 (RFP Shredder)"""

    # 废标条款正则：匹配包含 ★、▲、必须、应当、否则无效、无效标 等关键词的句子
    DISQUALIFICATION_PATTERN = re.compile(
        r"([^。；\n]*(?:★|▲|必须|应当|否则按无效标处理|否则无效|否决投标|一票否决|不予受理|废标)[^。；\n]*[。；]?)",
        re.IGNORECASE
    )

    # 评分点正则：匹配包含 评分、得X分、满分 等关键词的句子
    SCORING_PATTERN = re.compile(
        r"([^。；\n]*(?:得\s*\d+\s*分|满分|评分标准)[^。；\n]*[。；]?)",
        re.IGNORECASE
    )

    def extract_clauses(self, chunks: list[ParsedChunk], ai_provider: AIProvider | None = None) -> list[Clause]:
        """双通道提取条款并融合结果。"""
        # 通道A：正则匹配
        channel_a_clauses = self._extract_channel_a(chunks)

        # 通道B：AI提取 (可选)
        channel_b_clauses = self._extract_channel_b(chunks, ai_provider) if ai_provider and ai_provider.available else []

        # 融合与分歧处理
        return self._merge_channels(channel_a_clauses, channel_b_clauses)

    def _extract_channel_a(self, chunks: list[ParsedChunk]) -> list[Clause]:
        clauses = []
        clause_idx = 1

        for chunk in chunks:
            # 提取废标项
            for match in self.DISQUALIFICATION_PATTERN.finditer(chunk.text):
                content = match.group(1).strip()
                if not content:
                    continue
                pointer = self._create_source_pointer(chunk, content)
                clauses.append(Clause(
                    id=f"regex_dq_{clause_idx}",
                    clause_type=ClauseType.DISQUALIFICATION,
                    content=content,
                    source_pointer=pointer,
                    confidence=0.8,  # 正则匹配确定性较高
                    needs_human_review=False
                ))
                clause_idx += 1

            # 提取评分点
            for match in self.SCORING_PATTERN.finditer(chunk.text):
                content = match.group(1).strip()
                if not content:
                    continue
                pointer = self._create_source_pointer(chunk, content)
                clauses.append(Clause(
                    id=f"regex_sc_{clause_idx}",
                    clause_type=ClauseType.SCORING,
                    content=content,
                    source_pointer=pointer,
                    confidence=0.8,
                    needs_human_review=False
                ))
                clause_idx += 1

        return clauses

    def _extract_channel_b(self, chunks: list[ParsedChunk], ai_provider: AIProvider) -> list[Clause]:
        clauses = []
        clause_idx = 1

        system_prompt = (
            "你是资深招标合规专家。请从给定的招标文件片段中提取所有：\n"
            "1. 废标项/资格要求（包含强制性条件、星号条款等，否则会导致投标无效的条款）。\n"
            "2. 评分点（具体的得分标准和分值）。\n"
            "请以严格的JSON数组格式返回，每个对象包含：\n"
            "- type: '废标项' 或 '评分点'\n"
            "- content: 提取的原句子内容"
        )

        for chunk in chunks:
            try:
                response = ai_provider.complete(
                    system=system_prompt,
                    user=chunk.text,
                    temperature=0.1
                )

                # 尝试解析JSON
                try:
                    # 寻找可能的 JSON 数组
                    json_str = response
                    start = response.find('[')
                    end = response.rfind(']')
                    if start != -1 and end != -1:
                        json_str = response[start:end+1]

                    items = json.loads(json_str)

                    for item in items:
                        c_type = item.get("type")
                        content = item.get("content", "").strip()
                        if not content:
                            continue

                        clause_type = ClauseType.UNKNOWN
                        if c_type == "废标项":
                            clause_type = ClauseType.DISQUALIFICATION
                        elif c_type == "评分点":
                            clause_type = ClauseType.SCORING

                        pointer = self._create_source_pointer(chunk, content)
                        clauses.append(Clause(
                            id=f"ai_{clause_idx}",
                            clause_type=clause_type,
                            content=content,
                            source_pointer=pointer,
                            confidence=0.7,
                            needs_human_review=False
                        ))
                        clause_idx += 1
                except json.JSONDecodeError:
                    # 忽略解析失败
                    pass
            except Exception:
                # 忽略调用失败
                pass

        return clauses

    def _merge_channels(self, channel_a: list[Clause], channel_b: list[Clause]) -> list[Clause]:
        merged = []

        # 简单根据内容合并
        seen_contents = set()

        # A 通道优先
        for clause in channel_a:
            content_norm = clause.content.strip().replace(" ", "")
            seen_contents.add(content_norm)

            # 检查B通道是否也找到了类似内容
            found_in_b = False
            for b_clause in channel_b:
                if b_clause.content.strip().replace(" ", "") in content_norm or content_norm in b_clause.content.strip().replace(" ", ""):
                    found_in_b = True
                    break

            if channel_b and not found_in_b:
                clause.needs_human_review = True

            merged.append(clause)

        # 补充 B 通道中独有的内容
        for clause in channel_b:
            content_norm = clause.content.strip().replace(" ", "")
            if content_norm not in seen_contents:
                # 只在 AI 通道被检出，需要人工复核
                clause.needs_human_review = True
                merged.append(clause)
                seen_contents.add(content_norm)

        return merged

    def _create_source_pointer(self, chunk: ParsedChunk, content: str) -> SourcePointer:
        from app.domain import SourceType, TrustLevel
        import uuid

        return SourcePointer(
            id=str(uuid.uuid4()),
            source_type=SourceType.file,
            source_name=chunk.metadata.get("source", "unknown"),
            page_number=chunk.page_number,
            section=chunk.section,
            original_text=content,
            file_name=chunk.metadata.get("file_name")
        )

    def export_compliance_matrix_csv(self, clauses: list[Clause]) -> str:
        """一键合规审查矩阵导出器 (CSV)"""
        output = io.StringIO()
        writer = csv.writer(output)

        # 写入表头
        writer.writerow(["条款类型", "条款内容", "来源文件", "页码", "章节", "人工复核要求", "响应状态/说明"])

        # 写入数据
        for clause in clauses:
            writer.writerow([
                clause.clause_type.value,
                clause.content,
                clause.source_pointer.file_name or clause.source_pointer.source_name or "-",
                f"P{clause.source_pointer.page_number}" if clause.source_pointer.page_number else "-",
                clause.source_pointer.section or "-",
                "需要" if clause.needs_human_review else "否",
                ""  # 留白供人工填写响应状态
            ])

        return output.getvalue()
