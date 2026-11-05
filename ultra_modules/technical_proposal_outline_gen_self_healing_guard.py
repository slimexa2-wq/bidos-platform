import time
import copy

class TechnicalProposalOutlineGenerator:
    """
    技术标大纲生成器，支持按权重分配篇幅，并包含静默自愈和5秒快照一键撤回（Undo）功能。
    """
    def __init__(self):
        self._current_outline = []
        self._snapshot = None
        self._snapshot_time = 0
        self.UNDO_WINDOW = 5.0

    def generate_outline(self, criteria=None, total_pages=100):
        # 保存快照以便支持Undo
        self._snapshot = copy.deepcopy(self._current_outline)
        self._snapshot_time = time.time()

        # 自愈：处理异常的 total_pages
        if not isinstance(total_pages, (int, float)) or total_pages <= 0:
            total_pages = 100
        else:
            total_pages = int(total_pages)

        # 自愈：处理异常的 criteria 输入
        if criteria is None:
            criteria = []
        elif not isinstance(criteria, list):
            if isinstance(criteria, dict):
                criteria = [criteria]
            else:
                criteria = []

        valid_criteria = []
        for c in criteria:
            if not isinstance(c, dict):
                continue

            # 处理章节名称
            name = c.get('name', '未命名章节')
            if not isinstance(name, str) or not name.strip():
                name = '未命名章节'
            else:
                name = name.strip()

            # 处理权重
            weight = c.get('weight', 10)
            if isinstance(weight, str):
                try:
                    weight = float(weight)
                except ValueError:
                    weight = 10
            elif not isinstance(weight, (int, float)):
                weight = 10

            # 权重不允许为负数，直接取绝对值自愈
            if weight < 0:
                weight = abs(weight)

            # 处理得分点
            points = c.get('points', ['符合标准'])
            if not isinstance(points, list):
                points = [str(points)]

            valid_criteria.append({
                'name': name,
                'weight': weight,
                'points': points
            })

        # 若所有输入均无效或为空，提供默认兜底大纲
        if not valid_criteria:
            valid_criteria = [{
                'name': '默认方案大纲',
                'weight': 100,
                'points': ['综合要求']
            }]

        total_weight = sum(c['weight'] for c in valid_criteria)
        # 如果总权重为0（例如所有权重都被设为0），自愈为均分
        if total_weight == 0:
            for c in valid_criteria:
                c['weight'] = 10
            total_weight = len(valid_criteria) * 10

        # 按权重分配篇幅
        outline = []
        for c in valid_criteria:
            # 保证至少分配1页
            pages = max(1, round(total_pages * (c['weight'] / total_weight)))
            outline.append({
                'section_name': c['name'],
                'weight': c['weight'],
                'allocated_pages': pages,
                'scoring_points': c['points']
            })

        self._current_outline = outline
        return self._current_outline

    def undo(self):
        """
        一键撤回功能：若在 5 秒窗口内，则恢复到上一次生成前的状态；
        超过 5 秒或无可用快照时，静默忽略，不打断用户操作。
        """
        if self._snapshot is not None:
            if time.time() - self._snapshot_time <= self.UNDO_WINDOW:
                self._current_outline = copy.deepcopy(self._snapshot)
                self._snapshot = None  # 撤销后清除快照
        return self._current_outline

    def get_current_outline(self):
        return self._current_outline
