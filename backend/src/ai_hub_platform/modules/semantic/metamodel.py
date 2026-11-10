"""模板元模型(薄版):定义"什么是一个合法的对象模板 / 指标定义"。

单一事实来源:连接器映射、对账工具、query_objects 参数、网关档位校验
全部消费本模块定义的结构。设计文档见 docs/design/01-metamodel.md。
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

PropertyType = Literal[
    "string", "text", "int", "decimal", "money",
    "date", "datetime", "bool", "ref", "enum",
]
Tier = Literal["看", "说", "做"]
Cardinality = Literal["1", "0..1", "1..n", "0..n"]
Domain = Literal["销售", "产品", "供应", "生产", "资源", "辅助"]


class Property(BaseModel):
    name: str
    type: PropertyType
    desc: str = ""
    ref: Optional[str] = None          # type=ref 时指向的对象名
    enum_values: list[str] = []
    sensitive: bool = False            # 默认脱敏字段(出网前置脱敏依据)

    @model_validator(mode="after")
    def check_type_extras(self) -> "Property":
        if self.type == "ref" and not self.ref:
            raise ValueError(f"属性 {self.name}: type=ref 必须声明 ref 目标对象")
        if self.type == "enum" and not self.enum_values:
            raise ValueError(f"属性 {self.name}: type=enum 必须声明 enum_values")
        return self


class Relation(BaseModel):
    name: str
    target: str
    cardinality: Cardinality = "0..n"
    desc: str = ""


class Action(BaseModel):
    """对象上允许 Agent 参与的动作;tier 是网关硬校验的治理档位。"""

    name: str
    desc: str
    tier: Tier


class DeriveRule(BaseModel):
    """派生规则:when 内多条件为 AND;值为 None 表示该列 IS NULL。"""

    when: dict[str, Optional[str]]     # 锚表列 -> 期望值(None = IS NULL)
    value: str


class DerivedField(BaseModel):
    """声明式决策表:规则有序、首个匹配生效;无匹配且无 default → 隔离区。

    刻意只支持 等值 / 判空 两种测试 —— 这是决策表,不是表达式语言;
    更复杂的推导等真实需求出现再议(见 docs/design/01 §3.1)。
    """

    rules: list[DeriveRule]
    default: Optional[str] = None


class SourceBinding(BaseModel):
    """源系统映射;status=verified 表示已经现场数据字典核对固化。

    约定:tables[0] 为锚表(对象一行 = 锚表一行);key_map / field_map 的值
    遵循映射表达式文法(解析器见 data2agent/mapping.py):
    "表.字段"、"表.字段 (join 锚表.外键)"、"表.字段 (map 源值→对象值 / ...)"。
    derived 为派生属性的决策表(如订单业务状态),映射应用阶段执行。

    status=disabled:现场该源暂无此表(如测试白名单缺 QUOTATION),整条 binding
    停用 —— 不进白名单、不推导水位、不参与映射与查询,但模板仍可正常加载。
    """

    source: str                        # digiwin_yifei / digiwin_e10 / excel_* ...
    tables: list[str] = []
    status: Literal["draft", "verified", "disabled"] = "draft"
    key_map: dict[str, str] = {}       # 对象 key -> 源字段
    field_map: dict[str, str] = {}     # 属性 -> 源字段/表达式
    derived: dict[str, DerivedField] = {}  # 属性 -> 决策表(锚表列驱动)
    watermark: Optional[str] = None    # 增量水位字段
    materializer: Optional[str] = None  # 场景预计算器;D2A_* 为内部结果表
    notes: str = ""

    @property
    def enabled(self) -> bool:
        """禁用的 binding 不参与抽取 / 映射 / 查询。"""
        return self.status != "disabled"

    @property
    def source_tables(self) -> list[str]:
        """真正需要从源系统抽取的表。

        materializer binding 中的 D2A_* 表都是数据平台内部结果表，不能进入
        ERP 适配器白名单；其余表是预计算器的只读输入。M2 可以读取 M1
        的结果表，因此内部表不只会出现在 tables[0]。
        """
        if not self.materializer:
            return self.tables
        return [table for table in self.tables if not table.startswith("D2A_")]


class ObjectTemplate(BaseModel):
    object: str
    display_name: str
    description: str = ""
    domain: Domain
    source_of_truth: str = "ERP"
    keys: list[str]
    properties: list[Property]
    states: list[str] = []
    relations: list[Relation] = []
    actions: list[Action] = []
    bindings: list[SourceBinding] = []
    knowledge_refs: list[str] = []
    custom_field_slots: int = 20       # 客户个性化字段槽位,模板不分叉

    @model_validator(mode="after")
    def check_integrity(self) -> "ObjectTemplate":
        prop_names = [p.name for p in self.properties]
        dupes = {n for n in prop_names if prop_names.count(n) > 1}
        if dupes:
            raise ValueError(f"{self.object}: 属性名重复 {sorted(dupes)}")
        for k in self.keys:
            if k not in prop_names:
                raise ValueError(f"{self.object}: key '{k}' 不在属性列表中")
        props = {p.name: p for p in self.properties}
        for b in self.bindings:
            for prop_name, spec in b.derived.items():
                prop = props.get(prop_name)
                if prop is None:
                    raise ValueError(
                        f"{self.object}: derived 属性 '{prop_name}' 不在属性列表中")
                if prop.type == "enum":
                    values = [r.value for r in spec.rules]
                    if spec.default is not None:
                        values.append(spec.default)
                    bad = [v for v in values if v not in prop.enum_values]
                    if bad:
                        raise ValueError(
                            f"{self.object}.{prop_name}: 派生值 {bad} 不在枚举 "
                            f"{prop.enum_values} 内(binding {b.source})")
        return self


class MetricDef(BaseModel):
    metric: str
    display_name: str
    status: Literal["certified", "draft", "deprecated"] = "draft"
    formula: str
    grain: list[str]
    dimensions: list[str] = []
    caveats: str = ""
    freshness_sla: str = "T+1"

    @field_validator("metric")
    @classmethod
    def metric_id_snake_case(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError(f"指标 id 须为 snake_case: {v}")
        return v


class TemplatePack(BaseModel):
    version: str
    objects: list[ObjectTemplate] = []
    metrics: list[MetricDef] = []

    def object_names(self) -> set[str]:
        return {o.object for o in self.objects}

    def cross_validate(self) -> list[str]:
        """跨对象引用完整性:relation / ref 必须指向已定义对象;指标 id 唯一。"""
        errors: list[str] = []
        names = self.object_names()
        for o in self.objects:
            for r in o.relations:
                if r.target not in names:
                    errors.append(f"{o.object}.relations.{r.name}: 目标对象 {r.target} 未定义")
            for p in o.properties:
                if p.type == "ref" and p.ref not in names:
                    errors.append(f"{o.object}.{p.name}: ref 目标 {p.ref} 未定义")
        ids = [m.metric for m in self.metrics]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            errors.append(f"指标 id 重复: {dup}")
        return errors
