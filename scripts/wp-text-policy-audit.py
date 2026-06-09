#!/usr/bin/env python3
"""
wp-text-policy-audit.py

Read-only WordPress text policy audit script.

This script fetches a list of public pages, extracts visible-ish text from HTML,
checks it against a simple YAML-like policy file, and writes a Markdown report.

It is designed for WordPress pre-launch QA, staging review, brand-name cleanup,
terminology consistency checks, typo detection, and content handover review.

It does not log in to WordPress, modify content, update the database, or change
server settings.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import html.parser
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_USER_AGENT = "wp-qa-audit-toolkit/0.1 (+read-only text policy audit)"


@dataclass
class PolicyRule:
    pattern: str
    rule_type: str = "text-policy"
    severity: str = "Review"
    recommendation: str = ""
    regex: bool = False
    case_sensitive: bool = False


@dataclass
class MatchRecord:
    pattern: str
    rule_type: str
    severity: str
    recommendation: str
    matched_text: str
    context: str
    line_no: int | None = None


@dataclass
class PageResult:
    url: str
    status_code: int | None = None
    final_url: str = ""
    title: str = ""
    error: str = ""
    matches: list[MatchRecord] = field(default_factory=list)


class VisibleTextParser(html.parser.HTMLParser):
    """
    Basic HTML text extractor.

    This intentionally avoids external dependencies. It is not a browser and
    does not execute JavaScript. It skips common non-visible tags such as script,
    style, svg, and noscript.
    """

    SKIP_TAGS = {
        "script",
        "style",
        "svg",
        "noscript",
        "template",
        "head",
        "meta",
        "link",
    }

    BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "br",
        "li",
        "ul",
        "ol",
        "table",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._text_parts: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()

        if tag_lower in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        if tag_lower == "title":
            self._in_title = True
            self._title_parts = []

        if tag_lower in self.BLOCK_TAGS:
            self._text_parts.append("\n")

        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        # Capture useful accessible text that often carries visible QA wording.
        for attr_name in ("alt", "title", "aria-label"):
            attr_value = attrs_dict.get(attr_name)
            if attr_value:
                self._text_parts.append(" ")
                self._text_parts.append(attr_value)
                self._text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if tag_lower == "title":
            self._in_title = False

        if tag_lower in self.BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return

        if self._in_title:
            self._title_parts.append(data)

        if data:
            self._text_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_space(" ".join(self._title_parts))

    @property
    def text(self) -> str:
        raw = "".join(self._text_parts)
        lines = [normalize_space(line) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def normalize_space(value: str) -> str:
    return " ".join(value.replace("\t", " ").split())


def read_page_list(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Page list not found: {path}")

    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)

    return urls


def fetch_url(
    url: str,
    timeout: int,
    user_agent: str,
) -> tuple[int | None, str, bytes, str]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", None)
            final_url = response.geturl()
            body = response.read()
            return status_code, final_url, body, ""
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, exc.geturl(), body, f"HTTPError: {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return None, "", b"", f"URLError: {exc.reason}"
    except TimeoutError:
        return None, "", b"", "TimeoutError"
    except Exception as exc:
        return None, "", b"", f"{type(exc).__name__}: {exc}"


def decode_html(body: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def load_policy_rules(path: Path) -> list[PolicyRule]:
    """
    Load a very small YAML subset.

    This parser supports the simple structure used by examples/text-policy.yml:

    rules:
      - pattern: "..."
        type: "..."
        severity: "..."
        recommendation: "..."
        regex: true
        case_sensitive: false

    It is intentionally dependency-free. It is not a full YAML parser.
    """

    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    rules: list[PolicyRule] = []
    current: dict[str, str] | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or line == "rules:":
            continue

        if line.startswith("- "):
            if current:
                rules.append(rule_from_dict(current))
            current = {}

            remainder = line[2:].strip()
            if remainder:
                key, value = parse_key_value(remainder)
                if key:
                    current[key] = value
            continue

        if current is not None and ":" in line:
            key, value = parse_key_value(line)
            if key:
                current[key] = value

    if current:
        rules.append(rule_from_dict(current))

    return rules


def parse_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        return "", ""

    key, value = line.split(":", 1)
    key = key.strip()
    value = value.strip()

    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        value = value[1:-1]

    value = value.replace('\\"', '"').replace("\\'", "'")
    return key, value


def str_to_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def rule_from_dict(data: dict[str, str]) -> PolicyRule:
    return PolicyRule(
        pattern=data.get("pattern", ""),
        rule_type=data.get("type", "text-policy"),
        severity=data.get("severity", "Review"),
        recommendation=data.get("recommendation", ""),
        regex=str_to_bool(data.get("regex", ""), default=False),
        case_sensitive=str_to_bool(data.get("case_sensitive", ""), default=False),
    )


def find_matches(text: str, rule: PolicyRule, context_chars: int) -> list[MatchRecord]:
    if not rule.pattern:
        return []

    flags = 0 if rule.case_sensitive else re.IGNORECASE

    try:
        if rule.regex:
            pattern = re.compile(rule.pattern, flags)
        else:
            pattern = re.compile(re.escape(rule.pattern), flags)
    except re.error as exc:
        return [
            MatchRecord(
                pattern=rule.pattern,
                rule_type=rule.rule_type,
                severity="High",
                recommendation=f"Invalid regex pattern: {exc}",
                matched_text=rule.pattern,
                context="",
            )
        ]

    records: list[MatchRecord] = []
    for match in pattern.finditer(text):
        start, end = match.span()
        context_start = max(0, start - context_chars)
        context_end = min(len(text), end + context_chars)
        context = normalize_report_text(text[context_start:context_end])
        matched_text = normalize_report_text(match.group(0))
        line_no = text.count("\n", 0, start) + 1

        records.append(
            MatchRecord(
                pattern=rule.pattern,
                rule_type=rule.rule_type,
                severity=rule.severity,
                recommendation=rule.recommendation,
                matched_text=matched_text,
                context=context,
                line_no=line_no,
            )
        )

    return records


def normalize_report_text(value: str) -> str:
    value = html.unescape(value)
    return " ".join(value.replace("\n", " ").replace("\t", " ").split())


def audit_page(
    page_url: str,
    rules: list[PolicyRule],
    timeout: int,
    user_agent: str,
    context_chars: int,
) -> PageResult:
    result = PageResult(url=page_url)

    status_code, final_url, body, error = fetch_url(
        url=page_url,
        timeout=timeout,
        user_agent=user_agent,
    )

    result.status_code = status_code
    result.final_url = final_url
    result.error = error

    if not body:
        return result

    html_text = decode_html(body)
    parser = VisibleTextParser()

    try:
        parser.feed(html_text)
    except Exception as exc:
        result.error = join_issue(
            result.error,
            f"HTML parse warning: {type(exc).__name__}: {exc}",
        )

    result.title = parser.title
    visible_text = parser.text

    for rule in rules:
        result.matches.extend(
            find_matches(
                text=visible_text,
                rule=rule,
                context_chars=context_chars,
            )
        )

    return result


def join_issue(existing: str, new: str) -> str:
    if existing and new:
        return existing.rstrip(".") + ". " + new
    return existing or new


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def severity_rank(severity: str) -> int:
    normalized = severity.strip().lower()
    if normalized == "high":
        return 0
    if normalized == "review":
        return 1
    if normalized == "info":
        return 2
    return 3


def generate_report(
    results: list[PageResult],
    rules: list[PolicyRule],
    args: argparse.Namespace,
) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total_matches = sum(len(r.matches) for r in results)
    high_matches = sum(
        1
        for result in results
        for match in result.matches
        if match.severity.strip().lower() == "high"
    )

    lines: list[str] = []
    lines.append("# WordPress Text Policy Audit Report")
    lines.append("")
    lines.append(f"Generated: {now}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Pages checked: {len(results)}")
    lines.append(f"- Policy rules loaded: {len(rules)}")
    lines.append(f"- Total matches: {total_matches}")
    lines.append(f"- High severity matches: {high_matches}")
    lines.append(f"- Policy file: `{args.policy}`")
    lines.append("")

    lines.append("## Page Results")
    lines.append("")

    for page in results:
        lines.append(f"### {page.url}")
        lines.append("")

        if page.title:
            lines.append(f"- Title: {page.title}")
        lines.append(f"- Page status: {page.status_code if page.status_code is not None else 'N/A'}")
        if page.final_url and page.final_url != page.url:
            lines.append(f"- Final URL: {page.final_url}")
        if page.error:
            lines.append(f"- Page fetch note: {page.error}")
        lines.append(f"- Matches: {len(page.matches)}")
        lines.append("")

        if not page.matches:
            lines.append("No text policy matches found on this page.")
            lines.append("")
            continue

        sorted_matches = sorted(
            page.matches,
            key=lambda item: (severity_rank(item.severity), item.rule_type, item.pattern),
        )

        lines.append("| Severity | Type | Matched Text | Line | Recommendation | Context |")
        lines.append("|---|---|---|---:|---|---|")

        for match in sorted_matches:
            line_display = str(match.line_no) if match.line_no is not None else ""
            lines.append(
                f"| {markdown_escape(match.severity)} "
                f"| {markdown_escape(match.rule_type)} "
                f"| `{markdown_escape(match.matched_text[:120])}` "
                f"| {line_display} "
                f"| {markdown_escape(match.recommendation[:180])} "
                f"| {markdown_escape(match.context[:220])} |"
            )

        lines.append("")

    lines.append("## Rule Summary")
    lines.append("")
    lines.append("| Severity | Type | Pattern | Recommendation |")
    lines.append("|---|---|---|---|")

    for rule in sorted(rules, key=lambda item: (severity_rank(item.severity), item.rule_type, item.pattern)):
        lines.append(
            f"| {markdown_escape(rule.severity)} "
            f"| {markdown_escape(rule.rule_type)} "
            f"| `{markdown_escape(rule.pattern)}` "
            f"| {markdown_escape(rule.recommendation)} |"
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- This audit is read-only.")
    lines.append("- JavaScript-rendered text may not be detected.")
    lines.append("- Page builder data that is not rendered into HTML may not be detected.")
    lines.append("- This report supports manual QA review; it does not replace browser testing.")
    lines.append("- Fixes should be applied manually in WordPress, the page builder, theme options, menus, forms, or templates as appropriate.")
    lines.append("")

    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only WordPress text policy audit tool for pre-launch QA."
    )

    parser.add_argument(
        "--pages",
        required=True,
        help="Path to a text file containing page URLs to audit.",
    )
    parser.add_argument(
        "--policy",
        required=True,
        help="Path to the text policy YAML file.",
    )
    parser.add_argument(
        "--report",
        default="reports/text-policy-audit.md",
        help="Path to the Markdown report file. Default: reports/text-policy-audit.md",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Request timeout in seconds. Default: 20",
    )
    parser.add_argument(
        "--context-chars",
        type=int,
        default=90,
        help="Number of characters to include around each match. Default: 90",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="Custom User-Agent header.",
    )

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    pages_path = Path(args.pages)
    policy_path = Path(args.policy)
    report_path = Path(args.report)

    try:
        page_urls = read_page_list(pages_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not page_urls:
        print("ERROR: No page URLs found in page list.", file=sys.stderr)
        return 1

    try:
        rules = load_policy_rules(policy_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rules = [rule for rule in rules if rule.pattern]

    if not rules:
        print("ERROR: No valid policy rules found.", file=sys.stderr)
        return 1

    results: list[PageResult] = []

    print(f"Starting text policy audit for {len(page_urls)} page(s)...")
    print(f"Policy rules loaded: {len(rules)}")

    for index, page_url in enumerate(page_urls, start=1):
        print(f"[{index}/{len(page_urls)}] {page_url}")

        result = audit_page(
            page_url=page_url,
            rules=rules,
            timeout=args.timeout,
            user_agent=args.user_agent,
            context_chars=args.context_chars,
        )

        results.append(result)

    report = generate_report(results, rules, args)

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
    except Exception as exc:
        print(f"ERROR: Failed to write report: {exc}", file=sys.stderr)
        return 1

    match_count = sum(len(page.matches) for page in results)

    print("")
    print(f"Report written: {report_path}")
    print(f"Pages checked: {len(results)}")
    print(f"Text policy matches: {match_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))