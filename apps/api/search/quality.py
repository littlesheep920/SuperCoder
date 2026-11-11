"""Resource quality check-up: deterministic, explained, report-only.

The schema decides whether a manifest is *valid*. It cannot tell whether a valid resource is any
use: a skill whose body is one sentence, an interface whose every field says "caller-supplied
input", a remote MCP server that never declares it talks to the network. This module checks for
those, one rule at a time, and says what it found and how to fix it.

What it is, and what it is not
------------------------------
* **Report, not gate** (PRD §7.1: correctness gates, quality reports). Nothing here blocks a
  lifecycle transition; a reviewer reads the findings next to the diff.
* **Static.** It reads the manifest and the files, nothing else. Signals that need running the
  resource or watching it being used — success rate in `resource_usage`, adoption, review
  outcomes — are a second layer this module does not pretend to cover.
* **Not the Evaluation subsystem** (ARCHITECTURE_FREEZE.md §4.6). It is a fixed rule set with a
  version, applied to a resource whose artifact hash is recorded with the result. It does not
  claim a resource works.
* **Actionable by the author.** Every rule names something the resource's own files can change.
  Platform gaps (an environment with no adapter yet) are not the author's to fix and are not
  checked here.

Boilerplate
-----------
"Long enough" is meaningless if the length is stock text. A sentence of prose, a line of code, or
an interface-field description that appears verbatim in at least `TEMPLATE_MIN_RESOURCES`
resources of the corpus carries no information about *this* resource, so it does not count
towards any length rule. That is the same rule, with the same threshold and the same splitter, the
duplicate detector applies (`duplicates.segments`), so "boilerplate" means one thing everywhere.

Scoring
-------
Each rule is a `warning` (weight 2: the resource is hard to use or govern as it stands) or a
`suggestion` (weight 1: it would be better found or trusted). A resource's summary is the passed
weight over the applicable weight. The summary orders a worklist; the findings are the result.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .duplicates import TEMPLATE_MIN_RESOURCES, segments

__all__ = [
    "CHECKS",
    "RULESET_VERSION",
    "Check",
    "CorpusContext",
    "Finding",
    "QualityReport",
    "QualityResource",
    "assess",
    "assess_corpus",
]

RULESET_VERSION = "resource-quality/1"

WARNING = "warning"
SUGGESTION = "suggestion"
WEIGHTS = {WARNING: 2, SUGGESTION: 1}

DIMENSIONS = ("可发现", "可用", "可治理", "可信")

#: Below these, a text says too little to act on. Counted after boilerplate is removed.
MIN_DESCRIPTION_CHARS = 30
MIN_FIELD_DESCRIPTION_CHARS = 15
MIN_BODY_SEGMENTS = 3

#: Tags that say nothing a filter does not already say.
_NON_INFORMATIVE_TAGS = frozenset({"example", "demo", "sample", "test"})
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_TITLE = re.compile(r"^#\s+\S")
_TEST_FILE = re.compile(r"(^|/)(tests?/.+\.py|test_[^/]+\.py|[^/]+_test\.py)$")
_REMOTE_TRANSPORTS = frozenset({"sse", "streamable-http"})
_UNBOUNDED = re.compile(r"^\s*(>=?)\s*\S+\s*$")


@dataclass(frozen=True, slots=True)
class QualityResource:
    """One resource as the check-up sees it: its manifest and its files.

    ``files`` holds the resource directory's files keyed by resource-relative path, without
    ``resource.yaml`` (the manifest is passed parsed), as `ManifestLoader` exposes them.
    """

    manifest: Mapping[str, Any]
    files: Mapping[str, str]
    artifact_hash: str | None = None

    @property
    def kind(self) -> str:
        return str(self.manifest.get("kind", ""))

    @property
    def slug(self) -> str:
        return str(self.manifest.get("slug", ""))

    @property
    def body(self) -> str:
        return str(self.files.get(str(self.manifest.get("entrypoint", "")), ""))

    @property
    def spec(self) -> Mapping[str, Any]:
        value = self.manifest.get(self.kind)
        return value if isinstance(value, Mapping) else {}

    def interface_fields(self) -> list[tuple[str, Mapping[str, Any]]]:
        """Inputs, outputs and prompt variables, each with where it was declared."""

        found: list[tuple[str, Mapping[str, Any]]] = []
        for section in ("inputs", "outputs"):
            for item in self.manifest.get(section, []) or []:
                if isinstance(item, Mapping):
                    found.append((section, item))
        if self.kind == "prompt":
            for item in self.spec.get("variables", []) or []:
                if isinstance(item, Mapping):
                    found.append(("prompt.variables", item))
        return found


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _body_segments(resource: QualityResource) -> list[str]:
    lines = [line for line in resource.body.splitlines() if not _TITLE.match(line)]
    return segments("\n".join(lines))


@dataclass(frozen=True, slots=True)
class CorpusContext:
    """What counts as boilerplate in the corpus a resource is judged against."""

    boilerplate_segments: frozenset[str] = frozenset()
    boilerplate_field_descriptions: frozenset[str] = frozenset()
    resources: int = 0
    min_resources: int = TEMPLATE_MIN_RESOURCES

    @classmethod
    def from_resources(
        cls, resources: Iterable[QualityResource], *, min_resources: int = TEMPLATE_MIN_RESOURCES
    ) -> "CorpusContext":
        """Count each segment and field description once per resource that contains it."""

        segment_counts: Counter[str] = Counter()
        field_counts: Counter[str] = Counter()
        total = 0
        for resource in resources:
            total += 1
            text = set(segments(str(resource.manifest.get("description", "")))) | set(_body_segments(resource))
            segment_counts.update(text)
            field_counts.update(
                {_normalize(item.get("description", "")) for _, item in resource.interface_fields()} - {""}
            )
        return cls(
            boilerplate_segments=frozenset(s for s, n in segment_counts.items() if n >= min_resources),
            boilerplate_field_descriptions=frozenset(s for s, n in field_counts.items() if n >= min_resources),
            resources=total,
            min_resources=min_resources,
        )


@dataclass(frozen=True, slots=True)
class Finding:
    check_id: str
    dimension: str
    severity: str
    passed: bool
    evidence: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "check": self.check_id,
            "dimension": self.dimension,
            "severity": self.severity,
            "passed": self.passed,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class Check:
    check_id: str
    dimension: str
    severity: str
    title: str
    why: str
    fix: str
    applies: Callable[[QualityResource], bool]
    evaluate: Callable[[QualityResource, CorpusContext], tuple[bool, tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class QualityReport:
    kind: str
    slug: str
    version: str
    artifact_hash: str | None
    findings: tuple[Finding, ...]
    ruleset: str = RULESET_VERSION

    @property
    def applicable_weight(self) -> int:
        return sum(WEIGHTS[finding.severity] for finding in self.findings)

    @property
    def passed_weight(self) -> int:
        return sum(WEIGHTS[finding.severity] for finding in self.findings if finding.passed)

    @property
    def failed(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if not finding.passed)

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "slug": self.slug,
            "version": self.version,
            "artifact_hash": self.artifact_hash,
            "ruleset": self.ruleset,
            "passed_weight": self.passed_weight,
            "applicable_weight": self.applicable_weight,
            "findings": [finding.to_json() for finding in self.findings],
        }


# --------------------------------------------------------------------------- rules


def _always(_: QualityResource) -> bool:
    return True


def _description_specific(resource: QualityResource, context: CorpusContext):
    kept = [s for s in segments(str(resource.manifest.get("description", ""))) if s not in context.boilerplate_segments]
    informative = " ".join(kept)
    if _normalize(informative) == _normalize(str(resource.manifest.get("name", ""))):
        return False, ("描述只是重复了名称",)
    if len(informative) < MIN_DESCRIPTION_CHARS:
        return False, (f"去掉通用套话后描述只剩 {len(informative)} 个字符（至少 {MIN_DESCRIPTION_CHARS}）",)
    return True, ()


def _aliases_present(resource: QualityResource, _: CorpusContext):
    aliases = [alias for alias in resource.manifest.get("aliases", []) or [] if str(alias).strip()]
    return bool(aliases), () if aliases else ("没有别名",)


def _tags_informative(resource: QualityResource, _: CorpusContext):
    useless = _NON_INFORMATIVE_TAGS | {resource.kind}
    tags = [str(tag) for tag in resource.manifest.get("tags", []) or [] if str(tag).casefold() not in useless]
    if len(tags) >= 2:
        return True, ()
    return False, (f"除类型名与 example/demo 外只有 {len(tags)} 个标签",)


def _has_interface(resource: QualityResource) -> bool:
    return bool(resource.interface_fields())


def _interface_described(resource: QualityResource, context: CorpusContext):
    problems: list[str] = []
    for section, item in resource.interface_fields():
        name = f"{section}.{item.get('name', '?')}"
        description = str(item.get("description", "")).strip()
        if not description:
            problems.append(f"{name}：没有说明")
        elif _normalize(description) in context.boilerplate_field_descriptions:
            problems.append(f"{name}：说明是语料中的通用套话")
        elif len(description) < MIN_FIELD_DESCRIPTION_CHARS:
            problems.append(f"{name}：说明只有 {len(description)} 个字符")
    return not problems, tuple(problems)


_TEXT_BODIES = frozenset({"skill", "prompt", "agent", "mcp", "workflow"})


def _body_substantive(resource: QualityResource, context: CorpusContext):
    described = set(segments(str(resource.manifest.get("description", ""))))
    kept = [
        segment
        for segment in _body_segments(resource)
        if segment not in context.boilerplate_segments and segment not in described
    ]
    if len(kept) >= MIN_BODY_SEGMENTS:
        return True, ()
    return False, (
        f"去掉标题、通用套话和描述原句后，正文只剩 {len(kept)} 句（至少 {MIN_BODY_SEGMENTS}）",
    )


def _prompt_variables_match(resource: QualityResource, _: CorpusContext):
    declared = {str(item.get("name")) for item in resource.spec.get("variables", []) or [] if isinstance(item, Mapping)}
    used = set(_PLACEHOLDER.findall(resource.body))
    evidence: list[str] = []
    if declared - used:
        evidence.append("声明了但正文未使用：" + "、".join(sorted(declared - used)))
    if used - declared:
        evidence.append("正文使用了但未声明：" + "、".join(sorted(used - declared)))
    return not evidence, tuple(evidence)


def _skill_constraints(resource: QualityResource, _: CorpusContext):
    constraints = [c for c in resource.spec.get("constraints", []) or [] if str(c).strip()]
    return bool(constraints), () if constraints else ("没有声明约束",)


def _tool_timeout(resource: QualityResource, _: CorpusContext):
    timeout = resource.spec.get("timeout_seconds")
    return timeout is not None, () if timeout is not None else ("没有 timeout_seconds",)


def _is_remote(resource: QualityResource) -> bool:
    if resource.kind == "mcp":
        return bool(resource.spec.get("url")) or resource.spec.get("transport") in _REMOTE_TRANSPORTS
    if resource.kind == "tool":
        return resource.spec.get("execution_mode") == "http"
    return False


def _permission_entries(resource: QualityResource) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for value in resource.manifest.get("permissions", []) or []:
        if isinstance(value, str):
            entries.append((value, "allow"))
        elif isinstance(value, Mapping):
            entries.append((str(value.get("name", "")), str(value.get("effect", "allow"))))
    return entries


def _network_declared(resource: QualityResource, _: CorpusContext):
    allowed = [name for name, effect in _permission_entries(resource) if name.startswith("network:") and effect == "allow"]
    if allowed:
        return True, ()
    where = resource.spec.get("url") or resource.spec.get("transport") or resource.spec.get("execution_mode")
    return False, (f"通过网络访问（{where}），但没有声明允许的 network:* 权限",)


def _has_dependencies(resource: QualityResource) -> bool:
    return bool(resource.manifest.get("dependencies"))


def _dependencies_bounded(resource: QualityResource, _: CorpusContext):
    problems = []
    for dependency in resource.manifest.get("dependencies", []) or []:
        version = str(dependency.get("version", ""))
        if version.strip() == "*" or _UNBOUNDED.match(version):
            problems.append(f"{dependency.get('kind')}/{dependency.get('slug')}：版本范围 {version!r} 没有上界")
    return not problems, tuple(problems)


def _dependencies_purpose(resource: QualityResource, _: CorpusContext):
    missing = [
        f"{dependency.get('kind')}/{dependency.get('slug')}"
        for dependency in resource.manifest.get("dependencies", []) or []
        if not str(dependency.get("purpose", "")).strip()
    ]
    return not missing, tuple(f"{name}：没有写用途" for name in missing)


def _tool_has_tests(resource: QualityResource, _: CorpusContext):
    tests = sorted(path for path in resource.files if _TEST_FILE.search(path))
    return bool(tests), () if tests else ("目录中没有测试文件（test_*.py、*_test.py 或 tests/）",)


def _kind(*kinds: str) -> Callable[[QualityResource], bool]:
    return lambda resource: resource.kind in kinds


CHECKS: tuple[Check, ...] = (
    Check("description.specific", "可发现", WARNING, "描述说清做什么",
          "检索与推荐都从描述里取证据；套话和重复名称的描述让关键词与语义两路都无从区分。",
          "用一两句话写清它解决什么问题、输入什么、产出什么，不要写\"这是一个示例\"之类的通用句。",
          _always, _description_specific),
    Check("aliases.present", "可发现", SUGGESTION, "有别名",
          "工程师按业务语言找能力（课题背景第三点），别名是名称之外的第二个入口，中文别名尤其有用。",
          "在 aliases 中加入团队里的常用叫法，例如中文名称或缩写。",
          _always, _aliases_present),
    Check("tags.informative", "可发现", SUGGESTION, "标签有区分度",
          "只有类型名或 example 的标签不能帮助筛选，结构化过滤会退化成全量列表。",
          "至少写两个说明领域或用途的标签，例如 can、diagnostics、code-review。",
          _always, _tags_informative),
    Check("interface.described", "可用", WARNING, "输入输出写清楚",
          "调用方（人或 Agent）只能按字段说明准备输入；通用套话等于没有说明。",
          "为每个输入、输出和提示词变量写出含义、格式与约束。",
          _has_interface, _interface_described),
    Check("body.substantive", "可用", WARNING, "正文有实质内容",
          "正文是 Agent 实际执行的说明；只有标题和一两句话时，效果取决于模型自己猜。",
          "写出步骤、判断标准、输出格式和至少一个例子。",
          lambda resource: resource.kind in _TEXT_BODIES, _body_substantive),
    Check("prompt.variables_match", "可用", WARNING, "提示词变量与正文一致",
          "声明了但没用的变量会让调用方白准备输入；用了但没声明的变量在渲染时会原样留下 {{...}}。",
          "让 prompt.variables 与正文中的 {{变量}} 一一对应。",
          _kind("prompt"), _prompt_variables_match),
    Check("skill.constraints", "可用", SUGGESTION, "技能写明约束",
          "约束告诉 Agent 什么不能做，是技能可控的前提。",
          "在 skill.constraints 中写出边界，例如不修改文件、只读输入、遇到缺失信息时先追问。",
          _kind("skill"), _skill_constraints),
    Check("tool.timeout", "可用", SUGGESTION, "工具设置超时",
          "没有超时的工具会让工作流节点无限等待，只能靠整次运行的超时结束。",
          "在 tool.timeout_seconds 中写出合理上限。",
          _kind("tool"), _tool_timeout),
    Check("permissions.network_declared", "可治理", WARNING, "远程访问声明网络权限",
          "权限声明是审核与运行时求交授权的依据；访问网络却不声明，审核者看不到这条数据外流路径。",
          "声明 network:access（或更具体的 network:*）并写明理由。",
          _is_remote, _network_declared),
    Check("dependencies.bounded", "可治理", WARNING, "依赖版本有上界",
          "没有上界的范围会在依赖发布不兼容的大版本后静默升级。",
          "使用精确版本或 ^、~ 范围，或写成 \">=1.2.0 <2.0.0\"。",
          _has_dependencies, _dependencies_bounded),
    Check("dependencies.purpose", "可治理", SUGGESTION, "依赖写明用途",
          "审核者需要知道为什么依赖它，才能判断能否替换或去掉。",
          "为每个依赖填写 purpose。",
          _has_dependencies, _dependencies_purpose),
    Check("tool.has_tests", "可信", WARNING, "工具带测试",
          "工具是确定性代码，测试是它\"做了它声称的事\"的唯一证据。",
          "在资源目录中加入 test_*.py，覆盖正常输入、边界与错误输入。",
          _kind("tool"), _tool_has_tests),
)

_BY_ID = {check.check_id: check for check in CHECKS}


def check_by_id(check_id: str) -> Check:
    return _BY_ID[check_id]


def assess(resource: QualityResource, context: CorpusContext | None = None) -> QualityReport:
    """Apply every applicable rule to one resource, in the fixed order of `CHECKS`."""

    context = context or CorpusContext()
    findings = []
    for check in CHECKS:
        if not check.applies(resource):
            continue
        passed, evidence = check.evaluate(resource, context)
        findings.append(Finding(check.check_id, check.dimension, check.severity, bool(passed), tuple(evidence)))
    return QualityReport(
        kind=resource.kind,
        slug=resource.slug,
        version=str(resource.manifest.get("version", "")),
        artifact_hash=resource.artifact_hash,
        findings=tuple(findings),
    )


@dataclass(frozen=True, slots=True)
class CorpusAssessment:
    context: CorpusContext
    reports: tuple[QualityReport, ...] = field(default_factory=tuple)


def assess_corpus(resources: Iterable[QualityResource]) -> CorpusAssessment:
    """Judge every resource against boilerplate counted over the same corpus, in a stable order."""

    ordered = sorted(resources, key=lambda resource: (resource.kind, resource.slug))
    context = CorpusContext.from_resources(ordered)
    return CorpusAssessment(context=context, reports=tuple(assess(resource, context) for resource in ordered))
