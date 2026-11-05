import re
from typing import Any, Dict

def assemble_and_convert_resume(payload: Any) -> Dict[str, Any]:
    """
    Assembles and converts resume data, mapping legacy schema to the standard schema,
    healing dirty data, assigning defaults, and recording an audit trace.
    """
    audit_trace = []

    if not isinstance(payload, dict):
        audit_trace.append(f"Input payload was not a dictionary (got {type(payload).__name__}), converting to empty dict.")
        payload = {}
    else:
        payload = dict(payload)

    result = {}

    key_mapping = {
        "fullname": "name",
        "employeename": "name",
        "姓名": "name",

        "position": "role",
        "jobtitle": "role",
        "岗位": "role",
        "职务": "role",

        "socialinsurance": "social_security_months",
        "socialsecurity": "social_security_months",
        "社保缴纳月数": "social_security_months",
        "socialsecuritymonths": "social_security_months",

        "certs": "certificates",
        "certifications": "certificates",
        "证书": "certificates",

        "experience": "years_of_experience",
        "years": "years_of_experience",
        "经验": "years_of_experience"
    }

    mapped_data = {}
    for k, v in payload.items():
        if not isinstance(k, str):
            k_str = str(k)
            audit_trace.append(f"Converted non-string key {k} to '{k_str}'")
            k = k_str

        clean_key = k.lower().replace("_", "").replace("-", "").replace(" ", "")

        target_key = clean_key
        for legacy, standard in key_mapping.items():
            if clean_key == legacy.lower().replace("_", ""):
                target_key = standard
                if k != standard:
                    audit_trace.append(f"Mapped legacy/dirty key '{k}' to '{standard}'")
                break

        mapped_data[target_key] = v

    name = mapped_data.get("name")
    if name is None or name == "":
        result["name"] = "未知员工"
        audit_trace.append("Assigned default '未知员工' to missing 'name'")
    elif not isinstance(name, str):
        result["name"] = str(name).strip()
        audit_trace.append(f"Converted 'name' from {type(name).__name__} to string")
    else:
        result["name"] = name.strip()

    role = mapped_data.get("role")
    if role is None or role == "":
        result["role"] = "未定岗"
        audit_trace.append("Assigned default '未定岗' to missing 'role'")
    elif not isinstance(role, str):
        result["role"] = str(role).strip()
        audit_trace.append(f"Converted 'role' from {type(role).__name__} to string")
    else:
        result["role"] = role.strip()

    ssm = mapped_data.get("social_security_months")
    if ssm is None:
        result["social_security_months"] = 0
        audit_trace.append("Assigned default 0 to missing 'social_security_months'")
    elif isinstance(ssm, int):
        if ssm < 0:
             audit_trace.append(f"Clamped negative 'social_security_months' {ssm} to 0")
             result["social_security_months"] = 0
        else:
             result["social_security_months"] = ssm
    elif isinstance(ssm, str):
        match = re.search(r'[-+]?\d+', ssm)
        if match:
            val = int(match.group())
            if val < 0:
                result["social_security_months"] = 0
                audit_trace.append(f"Extracted integer {val} from string '{ssm}' for 'social_security_months', then clamped negative to 0")
            else:
                result["social_security_months"] = val
                audit_trace.append(f"Extracted integer {val} from string '{ssm}' for 'social_security_months'")
        else:
            result["social_security_months"] = 0
            audit_trace.append(f"Failed to extract integer from '{ssm}', defaulted 'social_security_months' to 0")
    else:
        try:
            val = int(ssm)
            if val < 0:
                 result["social_security_months"] = 0
                 audit_trace.append(f"Converted 'social_security_months' from {type(ssm).__name__} to negative int {val}, then clamped to 0")
            else:
                 result["social_security_months"] = val
                 audit_trace.append(f"Converted 'social_security_months' from {type(ssm).__name__} to {val}")
        except (ValueError, TypeError):
            result["social_security_months"] = 0
            audit_trace.append(f"Failed to convert {ssm} to int, defaulted 'social_security_months' to 0")

    certs = mapped_data.get("certificates")
    if certs is None:
        result["certificates"] = []
        audit_trace.append("Assigned default empty list to missing 'certificates'")
    elif isinstance(certs, list):
        clean_certs = []
        for c in certs:
            if isinstance(c, str):
                if c.strip():
                    clean_certs.append(c.strip())
            else:
                clean_certs.append(str(c).strip())
                audit_trace.append(f"Converted certificate item from {type(c).__name__} to string")
        result["certificates"] = clean_certs
    elif isinstance(certs, str):
        split_certs = [c.strip() for c in certs.split(',') if c.strip()]
        result["certificates"] = split_certs
        audit_trace.append("Split comma-separated string into 'certificates' list")
    else:
        result["certificates"] = [str(certs).strip()]
        audit_trace.append(f"Wrapped single non-string/non-list value of type {type(certs).__name__} into list for 'certificates'")

    for k, v in mapped_data.items():
        if k not in ["name", "role", "social_security_months", "certificates"]:
            result[k] = v

    result["_audit_trace"] = audit_trace
    return result
