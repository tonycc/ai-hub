"""模板校验 CLI:python -m data2agent.shared.metamodel.validate [templates 目录]"""

from __future__ import annotations

import sys

from .loader import TemplateLoadError, load_pack


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "templates"
    try:
        pack = load_pack(root)
    except TemplateLoadError as e:
        print("模板校验失败:")
        for err in e.errors:
            print(f"  - {err}")
        return 1
    print(f"模板校验通过:pack v{pack.version},对象 {len(pack.objects)} 个,指标 {len(pack.metrics)} 个")
    drafts = [
        f"{o.object}:{b.source}"
        for o in pack.objects
        for b in o.bindings
        if b.status == "draft"
    ]
    if drafts:
        print(f"提示:{len(drafts)} 个 binding 处于 draft,待现场数据字典核对后置为 verified")
        for d in drafts:
            print(f"  - {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
