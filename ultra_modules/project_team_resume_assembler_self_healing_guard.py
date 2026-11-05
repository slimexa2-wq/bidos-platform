import time
import copy
import uuid

class ProjectTeamResumeAssembler:
    """
    项目团队人员（拟派项目经理与驻厂专员）社保缴纳证明与资格证书汇编核对器。
    具备异常输入静默自愈与5秒一键撤回(Undo)轻量快照机制。
    """
    def __init__(self):
        self._snapshots = {}

    def assemble(self, raw_data):
        self._clean_expired_snapshots()

        snapshot_id = str(uuid.uuid4())
        self._snapshots[snapshot_id] = {
            "timestamp": time.time(),
            "original_data": copy.deepcopy(raw_data)
        }

        # 静默自愈：如果不是字典，强制转化或给默认兜底
        if not isinstance(raw_data, dict):
            if raw_data is None:
                raw_data = {}
            else:
                raw_data = {"unrecognized_input": raw_data}

        # 提取项目经理数据
        manager_raw = raw_data.get("project_manager")
        if not isinstance(manager_raw, dict):
            manager_raw = {"name": str(manager_raw)} if manager_raw else {}

        # 提取驻厂专员数据
        commissioner_raw = raw_data.get("resident_commissioner")
        if not isinstance(commissioner_raw, dict):
            commissioner_raw = {"name": str(commissioner_raw)} if commissioner_raw else {}

        manager_name = str(manager_raw.get("name") or "未命名项目经理").strip()
        manager_social = self._to_bool(manager_raw.get("social_security"))
        manager_certs = self._to_clean_list(manager_raw.get("certificates"))

        commissioner_name = str(commissioner_raw.get("name") or "未命名驻厂专员").strip()
        commissioner_social = self._to_bool(commissioner_raw.get("social_security"))
        commissioner_certs = self._to_clean_list(commissioner_raw.get("certificates"))

        return {
            "snapshot_id": snapshot_id,
            "project_manager": {
                "name": manager_name,
                "has_social_security": manager_social,
                "certificates": manager_certs
            },
            "resident_commissioner": {
                "name": commissioner_name,
                "has_social_security": commissioner_social,
                "certificates": commissioner_certs
            },
            "status": "Assembled Successfully"
        }

    def undo(self, snapshot_id):
        self._clean_expired_snapshots()

        if not snapshot_id or not isinstance(snapshot_id, str):
            return {"success": False, "message": "无效的快照ID", "data": None}

        if snapshot_id not in self._snapshots:
            return {"success": False, "message": "撤销失败：快照不存在或已超过5秒有效期", "data": None}

        snapshot = self._snapshots.pop(snapshot_id)
        return {"success": True, "message": "撤销成功", "data": snapshot["original_data"]}

    def _clean_expired_snapshots(self):
        current_time = time.time()
        expired_keys = [k for k, v in self._snapshots.items() if current_time - v["timestamp"] > 5.0]
        for k in expired_keys:
            del self._snapshots[k]

    def _to_bool(self, val):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ['true', 'yes', '1', '有', '是', 'y']
        if isinstance(val, (int, float)):
            return bool(val)
        return False

    def _to_clean_list(self, val):
        if val is None:
            return []
        if isinstance(val, str):
            val_stripped = val.strip()
            return [val_stripped] if val_stripped else []
        if not isinstance(val, (list, tuple, set)):
            return [str(val).strip()]
        return [str(v).strip() for v in val if v is not None and str(v).strip()]
