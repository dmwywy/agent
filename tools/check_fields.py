# tools/check_fields.py
"""字段字典一致性自检：确保 rules.yaml 引用的字段都在 fields.yaml 中，且无重复 code。"""
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ROOT / "rules" / "fields.yaml"
RULES = ROOT / "rules" / "rules.yaml"


def load(p: Path) -> dict:
    if not p.exists():
        print(f"[跳过] 未找到 {p.relative_to(ROOT)}")
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def main() -> int:
    f = load(FIELDS)
    codes = set(f.get("fields", {}).keys())
    print(f"字段字典：{len(codes)} 个字段，版本 {f.get('version')}")

    # [1] 字段自身完整性
    errs = []
    for code, spec in f.get("fields", {}).items():
        if spec.get("type") is None:
            errs.append(f"{code}: 缺少 type")
        if spec.get("period_type") not in {"FY", "YTD", "BAL_END", "BAL_BEG"}:
            errs.append(f"{code}: period_type 不合法（{spec.get('period_type')}）")
        if spec.get("source") == "NOTE" and not spec.get("note_ref"):
            errs.append(f"{code}: source 为 NOTE 但缺少 note_ref")
        if spec.get("type") == "amount" and spec.get("unit_hint") in {"%", "元/股"}:
            errs.append(f"{code}: 金额字段不应使用非金额单位")
    print(f"[1] 字段自身检查：{'通过' if not errs else '发现问题'}")
    for e in errs:
        print("   -", e)

    # [2] 规则的 requires 是否都在字典里
    r = load(RULES)
    missing = []
    for rule in r.get("rules", []):
        for req in rule.get("requires", []):
            if req not in codes:
                missing.append(f"{rule.get('rule_id')} 引用了未定义字段 {req}")
    print(f"[2] 规则引用检查：{'通过' if not missing else '发现问题'}")
    for m in missing:
        print("   -", m)

    ok = not errs and not missing
    print("=" * 48)
    print("结论：" + ("字段字典与规则表一致" if ok else "存在不一致，请修正后重跑"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())