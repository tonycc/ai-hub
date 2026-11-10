"""v0.3 版本身份工具。

binding hash 只描述会影响映射运行结果的契约内容，不依赖 YAML 排版、
Python 字典插入顺序或人读 notes。derived.rules 的列表顺序必须保留，
因为映射引擎按“首个匹配生效”执行。

数据集/对象版本记录是落地库只读查询的类型化投影；时间字段保持落库
legacy local ISO text，不在此层伪造时区或合成版本号。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schema import SourceBinding

BINDING_HASH_SCHEMA = "data2agent.binding.v1"

DatasetStatus = Literal["building", "published", "failed", "retired"]
ObjectBuildStatus = Literal["building", "built", "failed", "published", "retired"]


class DatasetVersionRecord(BaseModel):
    """d2a_dataset_version 行投影；空库不得伪造记录。"""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    source: str
    template_version: str
    status: DatasetStatus
    built_at: str = Field(description="legacy local ISO text from SQLite")
    published_at: str | None = Field(
        default=None, description="legacy local ISO text from SQLite")
    previous_dataset_version: str | None = None
    error: str | None = None
    object_manifest: str | None = Field(
        default=None,
        description="构建时冻结的对象名 JSON 数组;完整性分母,与当前模板无关",
    )
    template_snapshot: str | None = Field(
        default=None,
        description="冻结 TemplatePack JSON;内部字段,不暴露 datasets API",
    )


class ObjectVersionRecord(BaseModel):
    """d2a_object_version 行投影；隶属真实 dataset_version。"""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    object: str
    object_version: str
    binding_hash: str
    row_count: int = Field(ge=0)
    batch_id: str | None = None
    build_table: str | None = None
    status: ObjectBuildStatus
    built_at: str = Field(description="legacy local ISO text from SQLite")
    published_at: str | None = Field(
        default=None, description="legacy local ISO text from SQLite")
    purged_at: str | None = Field(
        default=None,
        description="物理表已清理时的 tombstone 时间;legacy local ISO text",
    )
    lineage_schema_version: int | None = Field(
        default=None,
        description="字段血缘 schema;旧版本 null,M4 新构建为 1",
    )
    lineage_field_count: int | None = Field(
        default=None,
        description="成功字段节点数;building 写入后冻结",
    )


def parse_object_manifest(raw: str | None) -> list[str] | None:
    """解析冻结对象清单;空/损坏/非唯一 → None(调用方 fail-closed)。"""
    if not raw or not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    names: list[str] = []
    for item in data:
        if not isinstance(item, str) or not item.strip():
            return None
        names.append(item)
    if len(names) != len(set(names)):
        return None
    return names


def object_layer_fully_published(
    dataset: DatasetVersionRecord, obj_rows: list[ObjectVersionRecord],
) -> bool:
    """对象层统一发布:清单齐全、无额外成员、且全部 published。

    分母来自数据集冻结的 object_manifest,不读当前模板;无可靠清单则 False。
    """
    manifest = parse_object_manifest(dataset.object_manifest)
    if not manifest:
        return False
    by_name = {o.object: o for o in obj_rows}
    if set(by_name) != set(manifest):
        return False
    return all(by_name[name].status == "published" for name in manifest)


def canonical_binding_json(binding: SourceBinding) -> str:
    """返回用于摘要的稳定 JSON，不包含不影响运行语义的 notes。"""
    semantic = binding.model_dump(mode="json", exclude={"notes"})
    payload = {"schema": BINDING_HASH_SCHEMA, "binding": semantic}
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def binding_hash(binding: SourceBinding) -> str:
    """返回带算法前缀的确定性 binding 内容摘要。"""
    digest = hashlib.sha256(canonical_binding_json(binding).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
