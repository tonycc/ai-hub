"""模板包加载与校验:templates/ 目录 -> TemplatePack。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import MetricDef, ObjectTemplate, TemplatePack


class TemplateLoadError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _read_yaml_file(path: Path) -> dict:
    """读取 YAML 文件;编码/语法错误时点名文件并给出修复指引。"""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise TemplateLoadError([
            f"{path.name}: 文件不是 UTF-8 编码(疑似 GBK/ANSI)——"
            f"用记事本「另存为 → 编码选 UTF-8」或从便携包 zip 还原该文件"
            f"(解码失败位置:{e})",
        ]) from e
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise TemplateLoadError([
            f"{path.name}: YAML 语法错误——{e}",
        ]) from e


def _template_yaml_files(directory: Path) -> list[Path]:
    """列出模板 YAML;跳过 macOS AppleDouble(._*)与隐藏文件。

    Mac 解压后拷 U 盘(FAT32/exFAT)会为每个文件生成 ._ 伴随文件,
    其二进制内容被 *.yaml 匹配读取会导致 UTF-8 解码崩溃。
    """
    return [
        f for f in sorted(directory.glob("*.yaml"))
        if not f.name.startswith(("._", "."))
    ]


def load_pack(root: str | Path) -> TemplatePack:
    root = Path(root)
    errors: list[str] = []
    objects: list[ObjectTemplate] = []
    metrics: list[MetricDef] = []

    pack_meta: dict = {}
    pack_file = root / "pack.yaml"
    if pack_file.exists():
        pack_meta = _read_yaml_file(pack_file)

    obj_dir = root / "objects"
    if obj_dir.is_dir():
        for f in _template_yaml_files(obj_dir):
            data = _read_yaml_file(f)
            try:
                objects.append(ObjectTemplate(**data))
            except ValidationError as e:
                errors.append(f"{f.name}: {e}")

    metric_dir = root / "metrics"
    if metric_dir.is_dir():
        for f in _template_yaml_files(metric_dir):
            data = _read_yaml_file(f)
            for m in data.get("metrics", []):
                try:
                    metrics.append(MetricDef(**m))
                except ValidationError as e:
                    errors.append(f"{f.name}: {e}")

    if errors:
        raise TemplateLoadError(errors)

    pack = TemplatePack(
        version=str(pack_meta.get("version", "0.0.0")),
        objects=objects,
        metrics=metrics,
    )
    cross = pack.cross_validate()
    if cross:
        raise TemplateLoadError(cross)
    return pack
