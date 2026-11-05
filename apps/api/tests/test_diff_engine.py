from app.services.diff_engine import compare_announcement_diff, _extract_amount, _extract_deadline, _extract_qualifications

def test_extract_amount():
    assert _extract_amount("项目预算金额：100.5万元") == 1005000.0
    assert _extract_amount("预算：50000元") == 50000.0
    assert _extract_amount("没有预算信息") is None

def test_extract_deadline():
    assert _extract_deadline("报名截止时间：2023-10-15 14:00") == "2023-10-15 14:00"
    assert _extract_deadline("递交投标文件截止时间: 2024/05/20") == "2024/05/20"
    assert _extract_deadline("没有任何时间") is None

def test_extract_qualifications():
    text = "资格条件：\n1. 具有独立承担民事责任的能力\n2. 良好的商业信誉\n\n三、其他事项"
    assert "具有独立承担民事责任的能力" in _extract_qualifications(text)

def test_compare_announcement_diff():
    old_text = "预算金额：100万元\n报名截止时间：2023-10-15"
    new_text = "预算金额：120万元\n报名截止时间：2023-10-20"

    diff = compare_announcement_diff(old_text, new_text)

    assert 'budget' in diff
    assert '1,200,000.00' in diff['budget']
    assert 'deadline' in diff
    assert '2023-10-20' in diff['deadline']

def test_compare_announcement_diff_no_change():
    old_text = "预算金额：100万元\n报名截止时间：2023-10-15"
    new_text = "预算金额：100万元\n报名截止时间：2023-10-15\n增加了一句话。"

    diff = compare_announcement_diff(old_text, new_text)

    assert 'budget' not in diff
    assert 'deadline' not in diff
