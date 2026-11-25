#!/usr/bin/env python3
"""Summarize JaCoCo, Cobertura, Clover, and lcov coverage reports. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

USAGE_EXIT = 2
FAIL_EXIT = 1
MAX_BYTES = 50 * 1024 * 1024

KNOWN_NAMES = (
    "jacoco.xml",
    "jacocotestreport.xml",
    "coverage.xml",
    "cobertura.xml",
    "clover.xml",
    "lcov.info",
    "coverage.lcov",
)

METRICS = ("line", "branch", "method", "instruction")
FORMATS = ("auto", "jacoco", "cobertura", "clover", "lcov")
DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE | re.DOTALL)


@dataclass
class Metric:
    covered: int = 0
    total: int = 0

    @property
    def missed(self) -> int:
        return max(0, self.total - self.covered)

    @property
    def pct(self) -> Optional[float]:
        if self.total <= 0:
            return None
        return 100.0 * self.covered / self.total

    def add(self, covered: int, total: int) -> None:
        self.covered += max(0, covered)
        self.total += max(0, total)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "covered": self.covered,
            "missed": self.missed,
            "total": self.total,
            "pct": None if self.pct is None else round(self.pct, 2),
        }


@dataclass
class FileCoverage:
    path: str
    package: str = ""
    line: Metric = field(default_factory=Metric)
    branch: Metric = field(default_factory=Metric)
    method: Metric = field(default_factory=Metric)
    instruction: Metric = field(default_factory=Metric)
    uncovered: List[int] = field(default_factory=list)
    line_hits: Dict[int, int] = field(default_factory=dict)

    def metric(self, name: str) -> Metric:
        return getattr(self, name)

    def uncovered_ranges(self) -> str:
        return compact_lines(self.uncovered)


@dataclass
class Report:
    files: List[FileCoverage] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    formats: List[str] = field(default_factory=list)
    line: Metric = field(default_factory=Metric)
    branch: Metric = field(default_factory=Metric)
    method: Metric = field(default_factory=Metric)
    instruction: Metric = field(default_factory=Metric)
    warnings: List[str] = field(default_factory=list)

    def metric(self, name: str) -> Metric:
        return getattr(self, name)


def local_tag(tag: Optional[str]) -> str:
    if not tag:
        return ""
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def compact_lines(nums: Sequence[int]) -> str:
    if not nums:
        return ""
    ordered = sorted(set(int(n) for n in nums if n > 0))
    if not ordered:
        return ""
    parts: List[str] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
        start = prev = n
    parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(parts)


def normalize_path(path: str) -> str:
    text = (path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def join_path(*parts: str) -> str:
    bits = [normalize_path(p).strip("/") for p in parts if p and normalize_path(p)]
    return "/".join(bit for bit in bits if bit)


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "     -"
    return "%5.1f%%" % value


def format_frac(metric: Metric) -> str:
    if metric.total <= 0:
        return "        -"
    return "%d/%d" % (metric.covered, metric.total)


def bar(value: Optional[float], width: int = 12) -> str:
    if value is None:
        return "[" + " " * width + "]"
    filled = int(round(value / 100.0 * width))
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def parse_int(value: Optional[str], default: int = 0) -> int:
    if value is None or value == "":
        return default
    text = str(value).strip()
    if text in {"-", "."}:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def strip_doctype(text: str) -> str:
    return DOCTYPE_RE.sub("", text, count=1)


def read_text(path: str) -> str:
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        size = os.path.getsize(path)
        if size > MAX_BYTES:
            raise ValueError("file larger than 50 MB, skipped: %s" % path)
        with open(path, "rb") as handle:
            data = handle.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def detect_format(text: str) -> str:
    stripped = text.lstrip("\ufeff \t\r\n")
    if not stripped:
        return "unknown"
    if stripped.startswith("<"):
        try:
            root = ET.fromstring(strip_doctype(stripped))
        except ET.ParseError:
            return "unknown"
        return detect_xml(root)
    for raw in stripped.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith((
            "TN:", "SF:", "DA:", "FN:", "FNF:", "FNH:",
            "LF:", "LH:", "BRF:", "BRH:", "end_of_record",
        )):
            return "lcov"
        break
    return "unknown"


def detect_xml(root: ET.Element) -> str:
    tag = local_tag(root.tag)
    if tag == "report":
        return "jacoco"
    if tag == "coverage":
        if root.get("clover") is not None:
            return "clover"
        for child in list(root)[:8]:
            name = local_tag(child.tag)
            if name == "project":
                return "clover"
            if name in {"packages", "sources"}:
                return "cobertura"
        if root.get("line-rate") is not None or root.get("lines-valid") is not None:
            return "cobertura"
        return "cobertura"
    return "unknown"


def metric_from_counters(elem: ET.Element) -> Dict[str, Metric]:
    out = {
        "line": Metric(),
        "branch": Metric(),
        "method": Metric(),
        "instruction": Metric(),
    }
    mapping = {
        "LINE": "line",
        "BRANCH": "branch",
        "METHOD": "method",
        "INSTRUCTION": "instruction",
    }
    for child in elem:
        if local_tag(child.tag) != "counter":
            continue
        key = mapping.get((child.get("type") or "").upper())
        if not key:
            continue
        missed = parse_int(child.get("missed"))
        covered = parse_int(child.get("covered"))
        out[key].add(covered, missed + covered)
    return out


def apply_metrics(file_cov: FileCoverage, metrics: Dict[str, Metric]) -> None:
    for name in METRICS:
        src = metrics[name]
        getattr(file_cov, name).add(src.covered, src.total)


def uncovered_from_hits(hits: Dict[int, int]) -> List[int]:
    return sorted(n for n, count in hits.items() if n > 0 and count <= 0)


def fallback_totals(files: List[FileCoverage], source: str, name: str, metrics: Dict[str, Metric]) -> List[FileCoverage]:
    if files:
        return files
    if not any(item.total for item in metrics.values()):
        return files
    item = FileCoverage(path=name or source)
    apply_metrics(item, metrics)
    return [item]


def parse_jacoco(root: ET.Element, source: str) -> List[FileCoverage]:
    files: List[FileCoverage] = []
    for package in root.iter():
        if local_tag(package.tag) != "package":
            continue
        pkg = (package.get("name") or "").replace(".", "/")
        for child in package:
            if local_tag(child.tag) != "sourcefile":
                continue
            name = child.get("name") or ""
            path = join_path(pkg, name) or name or source
            item = FileCoverage(path=path, package=pkg.replace("/", "."))
            hits: Dict[int, int] = {}
            for line in child:
                if local_tag(line.tag) != "line":
                    continue
                nr = parse_int(line.get("nr"))
                if nr <= 0:
                    continue
                covered_ins = parse_int(line.get("ci"))
                missed_ins = parse_int(line.get("mi"))
                missed_br = parse_int(line.get("mb"))
                if covered_ins <= 0 and missed_ins <= 0 and missed_br <= 0:
                    continue
                hits[nr] = covered_ins
            item.line_hits = hits
            item.uncovered = uncovered_from_hits(hits)
            metrics = metric_from_counters(child)
            if metrics["line"].total == 0 and hits:
                covered = sum(1 for count in hits.values() if count > 0)
                metrics["line"] = Metric(covered=covered, total=len(hits))
            apply_metrics(item, metrics)
            files.append(item)
    report_metrics = metric_from_counters(root)
    return fallback_totals(files, source, root.get("name") or source, report_metrics)


def parse_condition_coverage(text: str, hits: int) -> Tuple[int, int]:
    match = re.search(r"\((\d+)/(\d+)\)", text or "")
    if match:
        return parse_int(match.group(1)), parse_int(match.group(2))
    if hits > 0:
        return 2, 2
    return 0, 2


def cobertura_lines(elem: ET.Element) -> Tuple[Dict[int, int], Metric, Metric]:
    hits: Dict[int, int] = {}
    branch = Metric()
    for line in elem:
        if local_tag(line.tag) != "line":
            continue
        nr = parse_int(line.get("number"))
        if nr <= 0:
            continue
        count = parse_int(line.get("hits"))
        hits[nr] = max(hits.get(nr, 0), count)
        cond = line.get("condition-coverage") or ""
        is_branch = (line.get("branch") or "").lower() == "true" or bool(cond)
        if is_branch:
            covered, total = parse_condition_coverage(cond, count)
            branch.add(covered, total)
    line_m = Metric(
        covered=sum(1 for count in hits.values() if count > 0),
        total=len(hits),
    )
    return hits, line_m, branch


def parse_cobertura(root: ET.Element, source: str) -> List[FileCoverage]:
    files: List[FileCoverage] = []
    for cls in root.iter():
        if local_tag(cls.tag) != "class":
            continue
        filename = cls.get("filename") or ""
        name = cls.get("name") or ""
        path = normalize_path(filename) or normalize_path(name.replace(".", "/")) or source
        if "." in name:
            pkg = name.rsplit(".", 1)[0]
        elif "/" in path:
            pkg = "/".join(normalize_path(path).split("/")[:-1]).replace("/", ".")
        else:
            pkg = ""
        item = FileCoverage(path=path, package=pkg)
        lines_elem = None
        methods_elem = None
        for child in cls:
            tag = local_tag(child.tag)
            if tag == "lines":
                lines_elem = child
            elif tag == "methods":
                methods_elem = child
        method_total = 0
        method_covered = 0
        hits: Dict[int, int] = {}
        line_m = Metric()
        branch_m = Metric()
        if methods_elem is not None:
            for method in methods_elem:
                if local_tag(method.tag) != "method":
                    continue
                method_total += 1
                method_lines = None
                for sub in method:
                    if local_tag(sub.tag) == "lines":
                        method_lines = sub
                        break
                if method_lines is not None:
                    method_hits, _, method_branch = cobertura_lines(method_lines)
                    if any(count > 0 for count in method_hits.values()):
                        method_covered += 1
                    branch_m.add(method_branch.covered, method_branch.total)
                    for nr, count in method_hits.items():
                        hits[nr] = max(hits.get(nr, 0), count)
                else:
                    rate = method.get("line-rate")
                    try:
                        if rate is not None and float(rate) > 0:
                            method_covered += 1
                    except ValueError:
                        pass
        if lines_elem is not None:
            hits, line_m, class_branch = cobertura_lines(lines_elem)
            if class_branch.total:
                branch_m = class_branch
        elif hits:
            line_m = Metric(
                covered=sum(1 for count in hits.values() if count > 0),
                total=len(hits),
            )
        item.line_hits = hits
        item.uncovered = uncovered_from_hits(hits)
        item.line = line_m
        item.branch = branch_m
        if method_total:
            item.method = Metric(covered=method_covered, total=method_total)
        files.append(item)
    totals = {
        "line": Metric(parse_int(root.get("lines-covered")), parse_int(root.get("lines-valid"))),
        "branch": Metric(parse_int(root.get("branches-covered")), parse_int(root.get("branches-valid"))),
        "method": Metric(),
        "instruction": Metric(),
    }
    return fallback_totals(files, source, source, totals)


def infer_package(path: str) -> str:
    parts = [p for p in normalize_path(path).split("/") if p]
    if parts:
        parts = parts[:-1]
    skip = {"src", "main", "java", "test", "resources", "scala", "kotlin", "js", "ts", "lib"}
    while parts and parts[0] in skip:
        parts = parts[1:]
    if "java" in parts:
        parts = parts[parts.index("java") + 1 :]
    return ".".join(parts)


def parse_clover(root: ET.Element, source: str) -> List[FileCoverage]:
    files: List[FileCoverage] = []
    for file_elem in root.iter():
        if local_tag(file_elem.tag) != "file":
            continue
        name = file_elem.get("name") or ""
        path = normalize_path(file_elem.get("path") or name) or source
        item = FileCoverage(path=path, package=infer_package(path))
        hits: Dict[int, int] = {}
        branch = Metric()
        method = Metric()
        for child in file_elem:
            tag = local_tag(child.tag)
            if tag == "metrics":
                stmts = parse_int(child.get("statements"))
                covered_stmts = parse_int(child.get("coveredstatements"))
                cond = parse_int(child.get("conditionals"))
                covered_cond = parse_int(child.get("coveredconditionals"))
                methods = parse_int(child.get("methods"))
                covered_methods = parse_int(child.get("coveredmethods"))
                if stmts:
                    item.line = Metric(covered=covered_stmts, total=stmts)
                if cond:
                    item.branch = Metric(covered=covered_cond, total=cond)
                if methods:
                    item.method = Metric(covered=covered_methods, total=methods)
            elif tag == "line":
                nr = parse_int(child.get("num") or child.get("number"))
                typ = (child.get("type") or "stmt").lower()
                if typ == "cond":
                    true_c = parse_int(child.get("truecount"), default=-1)
                    false_c = parse_int(child.get("falsecount"), default=-1)
                    if child.get("truecount") is not None:
                        true_c = parse_int(child.get("truecount"))
                    if child.get("falsecount") is not None:
                        false_c = parse_int(child.get("falsecount"))
                    total = 0
                    covered = 0
                    if child.get("truecount") is not None and true_c >= 0:
                        total += 1
                        if true_c > 0:
                            covered += 1
                    if child.get("falsecount") is not None and false_c >= 0:
                        total += 1
                        if false_c > 0:
                            covered += 1
                    if total:
                        branch.add(covered, total)
                    count = max(0, true_c, false_c, parse_int(child.get("count")))
                    if nr > 0:
                        hits[nr] = max(hits.get(nr, 0), count)
                elif typ == "method":
                    count = parse_int(child.get("count"))
                    method.add(1 if count > 0 else 0, 1)
                    if nr > 0:
                        hits[nr] = max(hits.get(nr, 0), count)
                else:
                    count = parse_int(child.get("count"))
                    if nr > 0:
                        hits[nr] = max(hits.get(nr, 0), count)
        item.line_hits = hits
        item.uncovered = uncovered_from_hits(hits)
        if item.line.total == 0 and hits:
            item.line = Metric(
                covered=sum(1 for count in hits.values() if count > 0),
                total=len(hits),
            )
        if item.branch.total == 0 and branch.total:
            item.branch = branch
        if item.method.total == 0 and method.total:
            item.method = method
        files.append(item)
    return files


def parse_lcov(text: str, source: str) -> List[FileCoverage]:
    files: List[FileCoverage] = []
    current: Optional[FileCoverage] = None
    fn_hits: Dict[str, int] = {}
    lf: Optional[int] = None
    lh: Optional[int] = None
    brf: Optional[int] = None
    brh: Optional[int] = None
    branch_from_da = Metric()

    def finish() -> None:
        nonlocal current, fn_hits, lf, lh, brf, brh, branch_from_da
        if current is None:
            return
        if current.line.total == 0 and current.line_hits:
            current.line = Metric(
                covered=sum(1 for count in current.line_hits.values() if count > 0),
                total=len(current.line_hits),
            )
        if lf is not None:
            current.line = Metric(covered=lh or 0, total=lf)
        if brf is not None:
            current.branch = Metric(covered=brh or 0, total=brf)
        elif branch_from_da.total:
            current.branch = branch_from_da
        if fn_hits:
            covered = sum(1 for count in fn_hits.values() if count > 0)
            current.method = Metric(covered=covered, total=len(fn_hits))
        current.uncovered = uncovered_from_hits(current.line_hits)
        files.append(current)
        current = None
        fn_hits = {}
        lf = lh = brf = brh = None
        branch_from_da = Metric()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "end_of_record":
            finish()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key == "SF":
            if current is not None:
                finish()
            path = normalize_path(value) or source
            current = FileCoverage(path=path, package=infer_package(path))
        elif current is None:
            continue
        elif key == "DA":
            parts = value.split(",")
            if len(parts) >= 2:
                nr = parse_int(parts[0])
                count = parse_int(parts[1])
                if nr > 0:
                    current.line_hits[nr] = max(current.line_hits.get(nr, 0), count)
        elif key == "FN":
            name = value.split(",", 1)[-1] if "," in value else value
            fn_hits.setdefault(name, 0)
        elif key == "FNDA":
            parts = value.split(",", 1)
            if len(parts) == 2:
                fn_hits[parts[1]] = max(fn_hits.get(parts[1], 0), parse_int(parts[0]))
        elif key == "LF":
            lf = parse_int(value)
        elif key == "LH":
            lh = parse_int(value)
        elif key == "BRF":
            brf = parse_int(value)
        elif key == "BRH":
            brh = parse_int(value)
        elif key == "BRDA":
            parts = value.split(",")
            if len(parts) >= 4:
                taken = parts[3].strip()
                missed = taken in {"0", "-"}
                branch_from_da.add(0 if missed else 1, 1)
    if current is not None:
        finish()
    return files


def parse_document(text: str, source: str, forced: str = "auto") -> Tuple[List[FileCoverage], str]:
    kind = forced if forced and forced != "auto" else detect_format(text)
    if kind == "unknown":
        raise ValueError("not a JaCoCo, Cobertura, Clover, or lcov report: %s" % source)
    if kind == "lcov":
        return parse_lcov(text, source), kind
    try:
        root = ET.fromstring(strip_doctype(text.lstrip("\ufeff")))
    except ET.ParseError as exc:
        raise ValueError("invalid XML in %s: %s" % (source, exc)) from exc
    if forced == "auto":
        detected = detect_xml(root)
        if detected != "unknown":
            kind = detected
    if kind == "jacoco":
        return parse_jacoco(root, source), kind
    if kind == "cobertura":
        return parse_cobertura(root, source), kind
    if kind == "clover":
        return parse_clover(root, source), kind
    raise ValueError("not a JaCoCo, Cobertura, Clover, or lcov report: %s" % source)


def is_known_name(filename: str) -> bool:
    lower = filename.lower()
    if lower in KNOWN_NAMES:
        return True
    if lower.startswith("jacoco") and lower.endswith(".xml"):
        return True
    if lower.endswith(".lcov"):
        return True
    return False


def discover(path: str, recursive: bool) -> List[str]:
    if path == "-" or os.path.isfile(path):
        return [path]
    if not os.path.isdir(path):
        raise ValueError("path not found: %s" % path)
    found: List[str] = []
    if recursive:
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv", "venv"}]
            for name in names:
                if is_known_name(name):
                    found.append(os.path.join(root, name))
    else:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full) and is_known_name(name):
                found.append(full)
    found.sort()
    return found


def merge_files(items: List[FileCoverage]) -> List[FileCoverage]:
    by_path: Dict[str, FileCoverage] = {}
    order: List[str] = []
    for item in items:
        key = item.path or "?"
        if key not in by_path:
            by_path[key] = item
            order.append(key)
            continue
        dest = by_path[key]
        for nr, count in item.line_hits.items():
            dest.line_hits[nr] = dest.line_hits.get(nr, 0) + count
        if dest.line_hits:
            dest.line = Metric(
                covered=sum(1 for count in dest.line_hits.values() if count > 0),
                total=len(dest.line_hits),
            )
            dest.uncovered = uncovered_from_hits(dest.line_hits)
        else:
            dest.line.add(item.line.covered, item.line.total)
        dest.branch.add(item.branch.covered, item.branch.total)
        dest.method.add(item.method.covered, item.method.total)
        dest.instruction.add(item.instruction.covered, item.instruction.total)
        if item.package and not dest.package:
            dest.package = item.package
    return [by_path[key] for key in order]


def sum_metrics(files: List[FileCoverage]) -> Tuple[Metric, Metric, Metric, Metric]:
    line = Metric()
    branch = Metric()
    method = Metric()
    instruction = Metric()
    for item in files:
        line.add(item.line.covered, item.line.total)
        branch.add(item.branch.covered, item.branch.total)
        method.add(item.method.covered, item.method.total)
        instruction.add(item.instruction.covered, item.instruction.total)
    return line, branch, method, instruction


def load_reports(
    paths: Sequence[str],
    recursive: bool = False,
    forced: str = "auto",
) -> Report:
    report = Report()
    collected: List[FileCoverage] = []
    if not paths:
        raise ValueError("no input path")
    for raw in paths:
        try:
            discovered = discover(raw, recursive)
        except ValueError as exc:
            report.warnings.append(str(exc))
            continue
        if not discovered:
            report.warnings.append("no coverage report found in %s" % raw)
            continue
        for path in discovered:
            label = "stdin" if path == "-" else path
            try:
                text = read_text(path)
                files, kind = parse_document(text, label, forced)
            except ValueError as exc:
                report.warnings.append(str(exc))
                continue
            if not files:
                report.warnings.append("no file entries in %s" % label)
                continue
            report.sources.append(label)
            if kind not in report.formats:
                report.formats.append(kind)
            collected.extend(files)
    report.files = merge_files(collected)
    report.line, report.branch, report.method, report.instruction = sum_metrics(report.files)
    return report


def threshold_passed(report: Report, metric: str, minimum: Optional[float]) -> Optional[bool]:
    if minimum is None:
        return None
    value = report.metric(metric).pct
    if value is None:
        return False
    return value + 1e-9 >= minimum


def render_file_row(item: FileCoverage, metric: str, show_uncovered: bool) -> List[str]:
    current = item.metric(metric)
    rows = [
        "  %s  %s  %s  %s"
        % (
            format_pct(current.pct),
            format_frac(current).rjust(10),
            bar(current.pct, 8),
            item.path,
        )
    ]
    if show_uncovered and item.uncovered:
        rows.append("           uncovered: %s" % item.uncovered_ranges())
    return rows


def render_text(
    report: Report,
    metric: str,
    minimum: Optional[float],
    top: int,
    show_uncovered: bool,
) -> str:
    lines: List[str] = []
    title = "Coverage"
    if report.formats:
        title += "  " + ", ".join(report.formats)
    if len(report.sources) == 1:
        title += "  " + report.sources[0]
    elif report.sources:
        title += "  %d reports" % len(report.sources)
    lines.append(title)
    lines.append("")

    rows = [
        ("Lines", report.line),
        ("Branches", report.branch),
        ("Methods", report.method),
    ]
    if report.instruction.total:
        rows.append(("Instructions", report.instruction))
    width = max(len(name) for name, _ in rows)
    for name, item in rows:
        lines.append(
            "  %s  %s  %s  %s"
            % (
                name.ljust(width),
                format_frac(item).rjust(10),
                format_pct(item.pct),
                bar(item.pct),
            )
        )

    passed = threshold_passed(report, metric, minimum)
    if minimum is not None:
        value = report.metric(metric).pct
        status = "PASS" if passed else "FAIL"
        suffix = "" if value is not None else "  (no %s data)" % metric
        lines.append("")
        lines.append("Threshold  %s >= %.1f%%  %s%s" % (metric, minimum, status, suffix))

    ranked = [item for item in report.files if item.metric(metric).total > 0]
    ranked.sort(
        key=lambda item: (
            item.metric(metric).pct if item.metric(metric).pct is not None else 101.0,
            item.path,
        )
    )
    below: List[FileCoverage] = []
    if minimum is not None:
        below = [
            item
            for item in ranked
            if item.metric(metric).pct is not None and item.metric(metric).pct + 1e-9 < minimum
        ]
        if below:
            lines.append("")
            lines.append("Below threshold (%d)" % len(below))
            shown = below if top <= 0 else below[:top]
            for item in shown:
                lines.extend(render_file_row(item, metric, show_uncovered))
            if top > 0 and len(below) > top:
                lines.append("  ... %d more" % (len(below) - top))

    below_paths = {item.path for item in below}
    rest = [item for item in ranked if item.path not in below_paths]
    if rest:
        lines.append("")
        lines.append("Worst files" if not below else "Other files")
        shown = rest if top <= 0 else rest[:top]
        for item in shown:
            lines.extend(render_file_row(item, metric, show_uncovered))
        if top > 0 and len(rest) > top:
            lines.append("  ... %d more" % (len(rest) - top))

    if report.warnings:
        lines.append("")
        lines.append("Warnings (%d)" % len(report.warnings))
        for warning in report.warnings:
            lines.append("  %s" % warning)
    if not report.files and not report.warnings:
        lines.append("")
        lines.append("No coverage data.")
    return "\n".join(lines).rstrip() + "\n"


def report_to_dict(report: Report, metric: str, minimum: Optional[float]) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "formats": list(report.formats),
        "sources": list(report.sources),
        "metrics": {
            "line": report.line.as_dict(),
            "branch": report.branch.as_dict(),
            "method": report.method.as_dict(),
        },
        "files": [
            {
                "path": item.path,
                "package": item.package,
                "line": item.line.as_dict(),
                "branch": item.branch.as_dict(),
                "method": item.method.as_dict(),
                "instruction": item.instruction.as_dict() if item.instruction.total else None,
                "uncovered": list(item.uncovered),
                "uncovered_ranges": item.uncovered_ranges(),
            }
            for item in report.files
        ],
        "warnings": list(report.warnings),
    }
    if report.instruction.total:
        data["metrics"]["instruction"] = report.instruction.as_dict()
    if minimum is not None:
        data["threshold"] = {
            "metric": metric,
            "min": minimum,
            "passed": bool(threshold_passed(report, metric, minimum)),
            "pct": None if report.metric(metric).pct is None else round(report.metric(metric).pct, 2),
        }
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cov-sum",
        description="Summarize JaCoCo, Cobertura, Clover, and lcov coverage reports. Stdlib only, no upload.",
    )
    parser.add_argument("paths", nargs="*", help="report file, directory, or - for stdin")
    parser.add_argument("-r", "--recursive", action="store_true", help="scan directories recursively")
    parser.add_argument(
        "--format",
        choices=FORMATS,
        default="auto",
        help="force report format (default: auto)",
    )
    parser.add_argument(
        "--metric",
        choices=METRICS,
        default="line",
        help="metric used for ranking and --min (default: line)",
    )
    parser.add_argument(
        "--min",
        type=float,
        metavar="PCT",
        help="fail if the chosen metric is below this percent",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        metavar="N",
        help="how many files to list (0 = all, default: 15)",
    )
    parser.add_argument(
        "--uncovered",
        action="store_true",
        help="list uncovered line ranges under each file",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when a report cannot be read or no data is found",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.paths:
        parser.error("please pass a report file, a directory, or - for stdin")
    if args.top < 0:
        parser.error("--top must be >= 0")
    if args.min is not None and args.min < 0:
        parser.error("--min must be >= 0")

    try:
        report = load_reports(args.paths, recursive=args.recursive, forced=args.format)
    except ValueError as exc:
        sys.stderr.write("%s\n" % exc)
        return USAGE_EXIT

    if args.json:
        sys.stdout.write(json.dumps(report_to_dict(report, args.metric, args.min), indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(report, args.metric, args.min, args.top, args.uncovered))

    if args.strict and (report.warnings or not report.files):
        return FAIL_EXIT
    if threshold_passed(report, args.metric, args.min) is False:
        return FAIL_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
