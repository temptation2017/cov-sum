#!/usr/bin/env python3
"""Tests for cov_sum. Stdlib unittest only."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import cov_sum as cs  # noqa: E402

EXAMPLES = ROOT / "examples"


class Helpers(unittest.TestCase):
    def test_compact_lines(self):
        self.assertEqual(cs.compact_lines([]), "")
        self.assertEqual(cs.compact_lines([21]), "21")
        self.assertEqual(cs.compact_lines([21, 22, 23, 40]), "21-23, 40")
        self.assertEqual(cs.compact_lines([5, 5, 7, 6]), "5-7")

    def test_detect_formats(self):
        self.assertEqual(cs.detect_format((EXAMPLES / "jacoco.xml").read_text()), "jacoco")
        self.assertEqual(cs.detect_format((EXAMPLES / "coverage.xml").read_text()), "cobertura")
        self.assertEqual(cs.detect_format((EXAMPLES / "clover.xml").read_text()), "clover")
        self.assertEqual(cs.detect_format((EXAMPLES / "lcov.info").read_text()), "lcov")
        self.assertEqual(cs.detect_format("not a report"), "unknown")


class ParseExamples(unittest.TestCase):
    def test_jacoco(self):
        report = cs.load_reports([str(EXAMPLES / "jacoco.xml")])
        self.assertEqual(report.formats, ["jacoco"])
        self.assertEqual(report.line.covered, 30)
        self.assertEqual(report.line.total, 44)
        self.assertAlmostEqual(report.line.pct or 0, 68.1818, places=2)
        self.assertEqual(report.branch.covered, 7)
        self.assertEqual(report.branch.total, 14)
        self.assertEqual(report.method.covered, 8)
        self.assertEqual(report.method.total, 11)
        self.assertEqual(report.instruction.covered, 84)
        self.assertEqual(report.instruction.total, 130)
        by_path = {item.path: item for item in report.files}
        self.assertEqual(by_path["com/example/shop/OrderService.java"].uncovered, [21, 22, 23, 40])
        self.assertEqual(by_path["com/example/shop/OrderService.java"].uncovered_ranges(), "21-23, 40")
        self.assertEqual(by_path["com/example/shop/web/OrderController.java"].line.covered, 5)
        self.assertEqual(by_path["com/example/shop/web/OrderController.java"].line.total, 15)
        self.assertEqual(by_path["com/example/shop/Cart.java"].line.pct, 100.0)

    def test_cobertura(self):
        report = cs.load_reports([str(EXAMPLES / "coverage.xml")])
        self.assertEqual(report.formats, ["cobertura"])
        self.assertEqual(report.line.covered, 30)
        self.assertEqual(report.line.total, 44)
        self.assertEqual(report.method.covered, 8)
        self.assertEqual(report.method.total, 11)
        self.assertEqual(report.branch.covered, 7)
        self.assertEqual(report.branch.total, 16)
        controller = next(item for item in report.files if item.path.endswith("OrderController.java"))
        self.assertEqual(controller.uncovered, list(range(13, 23)))

    def test_clover(self):
        report = cs.load_reports([str(EXAMPLES / "clover.xml")])
        self.assertEqual(report.formats, ["clover"])
        self.assertEqual(report.line.covered, 30)
        self.assertEqual(report.line.total, 44)
        self.assertEqual(report.branch.covered, 7)
        self.assertEqual(report.branch.total, 14)
        self.assertEqual(report.method.covered, 8)
        self.assertEqual(report.method.total, 11)
        service = next(item for item in report.files if item.path.endswith("OrderService.java"))
        self.assertTrue(service.path.startswith("src/main/java/"))
        self.assertEqual(service.uncovered_ranges(), "21-23, 40")

    def test_lcov(self):
        report = cs.load_reports([str(EXAMPLES / "lcov.info")])
        self.assertEqual(report.formats, ["lcov"])
        self.assertEqual(report.line.covered, 30)
        self.assertEqual(report.line.total, 44)
        self.assertEqual(report.method.covered, 8)
        self.assertEqual(report.method.total, 11)
        self.assertEqual(report.branch.covered, 7)
        self.assertEqual(report.branch.total, 16)

    def test_namespaced_jacoco(self):
        report = cs.load_reports([str(EXAMPLES / "namespaced.xml")])
        self.assertEqual(report.formats, ["jacoco"])
        self.assertEqual(report.line.covered, 1)
        self.assertEqual(report.line.total, 2)
        self.assertEqual(report.files[0].path, "demo/Demo.java")
        self.assertEqual(report.files[0].uncovered, [2])


class MergeAndScan(unittest.TestCase):
    def test_merge_union_of_lines(self):
        first = """TN:
SF:src/app.js
DA:1,1
DA:2,0
LF:2
LH:1
end_of_record
"""
        second = """TN:
SF:src/app.js
DA:1,0
DA:2,3
LF:2
LH:1
end_of_record
"""
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.lcov"
            b = Path(tmp) / "b.lcov"
            a.write_text(first)
            b.write_text(second)
            report = cs.load_reports([str(a), str(b)])
            self.assertEqual(report.line.covered, 2)
            self.assertEqual(report.line.total, 2)
            self.assertEqual(report.files[0].uncovered, [])

    def test_directory_scan_known_names(self):
        report = cs.load_reports([str(EXAMPLES)], recursive=False)
        # namespaced.xml is not a conventional filename and is ignored
        self.assertGreaterEqual(len(report.sources), 4)
        self.assertFalse(any(src.endswith("namespaced.xml") for src in report.sources))
        self.assertIn("jacoco", report.formats)
        self.assertIn("lcov", report.formats)


class Cli(unittest.TestCase):
    def test_min_fail_and_pass(self):
        fail = cs.main([str(EXAMPLES / "jacoco.xml"), "--min", "80"])
        self.assertEqual(fail, 1)
        ok = cs.main([str(EXAMPLES / "jacoco.xml"), "--min", "60"])
        self.assertEqual(ok, 0)

    def test_json_and_threshold(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = cs.main([str(EXAMPLES / "jacoco.xml"), "--json", "--min", "80"])
        self.assertEqual(code, 1)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["formats"], ["jacoco"])
        self.assertEqual(data["metrics"]["line"]["covered"], 30)
        self.assertFalse(data["threshold"]["passed"])
        self.assertEqual(data["threshold"]["metric"], "line")

    def test_stdin(self):
        text = (EXAMPLES / "lcov.info").read_text()
        buf = io.StringIO()
        stdin = mock.Mock()
        stdin.buffer = io.BytesIO(text.encode("utf-8"))
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", buf):
            code = cs.main(["-"])
        self.assertEqual(code, 0)
        self.assertIn("lcov", buf.getvalue())
        self.assertIn("30/44", buf.getvalue())

    def test_strict_unknown_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "notes.txt"
            junk.write_text("hello")
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                code = cs.main([str(junk), "--strict"])
            self.assertEqual(code, 1)
            self.assertIn("Warnings", buf.getvalue())

    def test_missing_path_is_warning(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = cs.main(["/tmp/cov-sum-does-not-exist-xyz", "--strict"])
        self.assertEqual(code, 1)
        self.assertIn("path not found", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
