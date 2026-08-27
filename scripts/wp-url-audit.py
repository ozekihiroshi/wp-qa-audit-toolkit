#!/usr/bin/env python3
"""
wp-url-audit.py

Read-only WordPress URL audit script.

This script fetches a list of public pages, extracts links from <a href="...">,
classifies them, optionally checks HTTP status codes, and writes a Markdown
report.

It does not log in to WordPress, modify content, update the database, or change
server settings.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html.parser
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_USER_AGENT = "wp-qa-audit-toolkit/0.1 (+read-only link audit)"

def configure_console_stream(stream: object) -> None:
    """Avoid Windows console encoding failures for Unicode paths and URLs."""
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(errors="backslashreplace")
    except (AttributeError, OSError, ValueError):
        # Redirected or test streams may not support reconfiguration.
        return


@dataclass
class LinkRecord:
    source_page: str
    raw_href: str
    resolved_url: str
    link_text: str
    category: str
    issue: str = ""
    status_code: int | None = None
    final_url: str = ""
    error: str = ""


@dataclass
class PageResult:
    url: str
    status_code: int | None = None
    final_url: str = ""
    title: str = ""
    error: str = ""
    links: list[LinkRecord] = field(default_factory=list)


class SimpleHTMLLinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._in_a = False
        self._current_href = ""
        self._current_text_parts: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower == "a":
            self._in_a = True
            self._current_href = attrs_dict.get("href", "")
            self._current_text_parts = []

        if tag_lower == "title":
            self._in_title = True
            self._title_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower == "a" and self._in_a:
            text = normalize_space(" ".join(self._current_text_parts))
            self.links.append(
                {
                    "href": self._current_href.strip(),
                    "text": text,
                }
            )
            self._in_a = False
            self._current_href = ""
            self._current_text_parts = []

        if tag_lower == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_text_parts.append(data)
        if self._in_title:
            self._title_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_space(" ".join(self._title_parts))


def normalize_space(value: str) -> str:
    return " ".join(value.replace("\n", " ").replace("\t", " ").split())


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
    method: str = "GET",
) -> tuple[int | None, str, bytes, str]:
    request = urllib.request.Request(
        url,
        method=method,
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


def domain_matches(host: str, domain: str) -> bool:
    host = host.lower().strip()
    domain = domain.lower().strip()
    return host == domain or host.endswith("." + domain)


def classify_link(
    source_page: str,
    href: str,
    staging_domain: str | None,
    live_domain: str | None,
    allowed_domains: set[str],
) -> tuple[str, str, str]:
    raw = href.strip()

    if raw == "":
        return "empty", source_page, "Empty href."

    lowered = raw.lower()

    if lowered == "#":
        return "placeholder", urllib.parse.urljoin(source_page, raw), "Placeholder href '#'."

    if lowered.startswith("#"):
        return "anchor", urllib.parse.urljoin(source_page, raw), ""

    if lowered.startswith("javascript:"):
        return "javascript", raw, "JavaScript link. Review manually."

    if lowered.startswith("mailto:"):
        return "email", raw, ""

    if lowered.startswith("tel:"):
        return "telephone", raw, ""

    if lowered.startswith(("data:", "blob:")):
        return "asset", raw, "Non-page link. Review only if unexpected."

    resolved = urllib.parse.urljoin(source_page, raw)
    parsed_source = urllib.parse.urlparse(source_page)
    parsed_resolved = urllib.parse.urlparse(resolved)

    if parsed_resolved.scheme not in {"http", "https"}:
        return "other", resolved, f"Unsupported URL scheme: {parsed_resolved.scheme}"

    issue_parts: list[str] = []

    source_host = parsed_source.netloc.lower()
    target_host = parsed_resolved.netloc.lower()

    if parsed_source.scheme == "https" and parsed_resolved.scheme == "http":
        issue_parts.append("HTTP link found on HTTPS page.")

    if parsed_resolved.fragment and parsed_resolved.fragment.lower() in {"footer", "bottom"}:
        issue_parts.append("Suspicious anchor target. Link may scroll to footer or bottom.")

    if live_domain and staging_domain:
        if domain_matches(target_host, live_domain) and not domain_matches(source_host, live_domain):
            issue_parts.append("Link points to live domain while reviewing staging.")

    if allowed_domains and not any(domain_matches(target_host, d) for d in allowed_domains):
        category = "external"
        issue_parts.append("External or unexpected domain.")
    elif target_host == source_host:
        category = "internal"
    else:
        category = "internal-other-domain"

    return category, resolved, " ".join(issue_parts)


def check_link_status(
    url: str,
    timeout: int,
    user_agent: str,
) -> tuple[int | None, str, str]:
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return None, "", "Skipped non-HTTP link."

    status_code, final_url, _body, error = fetch_url(
        url=url,
        timeout=timeout,
        user_agent=user_agent,
        method="HEAD",
    )

    if status_code in {405, 403} or status_code is None:
        status_code, final_url, _body, error = fetch_url(
            url=url,
            timeout=timeout,
            user_agent=user_agent,
            method="GET",
        )

    return status_code, final_url, error


def join_issue(existing: str, new: str) -> str:
    if existing and new:
        return existing.rstrip(".") + ". " + new
    return existing or new


def audit_page(
    page_url: str,
    timeout: int,
    user_agent: str,
    check_status: bool,
    staging_domain: str | None,
    live_domain: str | None,
    allowed_domains: set[str],
    delay: float,
) -> PageResult:
    result = PageResult(url=page_url)

    status_code, final_url, body, error = fetch_url(
        url=page_url,
        timeout=timeout,
        user_agent=user_agent,
        method="GET",
    )

    result.status_code = status_code
    result.final_url = final_url
    result.error = error

    if not body:
        return result

    html_text = decode_html(body)
    parser = SimpleHTMLLinkParser()

    try:
        parser.feed(html_text)
    except Exception as exc:
        result.error = join_issue(
            result.error,
            f"HTML parse warning: {type(exc).__name__}: {exc}",
        )

    result.title = parser.title

    for item in parser.links:
        href = item.get("href", "")
        text = item.get("text", "")

        category, resolved, issue = classify_link(
            source_page=final_url or page_url,
            href=href,
            staging_domain=staging_domain,
            live_domain=live_domain,
            allowed_domains=allowed_domains,
        )

        record = LinkRecord(
            source_page=page_url,
            raw_href=href,
            resolved_url=resolved,
            link_text=text,
            category=category,
            issue=issue,
        )

        if check_status and category in {"internal", "internal-other-domain", "external"}:
            time.sleep(delay)
            link_status, link_final_url, link_error = check_link_status(
                url=resolved,
                timeout=timeout,
                user_agent=user_agent,
            )

            record.status_code = link_status
            record.final_url = link_final_url
            record.error = link_error

            if link_status is None:
                record.issue = join_issue(record.issue, "Could not check link status.")
            elif link_status >= 400:
                record.issue = join_issue(record.issue, f"HTTP status {link_status}.")

        result.links.append(record)

    return result


def issue_level(record: LinkRecord) -> str:
    if record.category in {"empty", "placeholder"}:
        return "High"
    if record.status_code and record.status_code >= 400:
        return "High"
    if "live domain" in record.issue.lower():
        return "High"
    if record.category == "javascript":
        return "Review"
    if record.issue:
        return "Review"
    return ""


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def generate_report(
    results: list[PageResult],
    args: argparse.Namespace,
) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total_links = sum(len(r.links) for r in results)
    issue_links = [
        link
        for result in results
        for link in result.links
        if link.issue or issue_level(link)
    ]
    high_links = [link for link in issue_links if issue_level(link) == "High"]

    lines: list[str] = []

    lines.append("# WordPress URL Audit Report")
    lines.append("")
    lines.append(f"Generated: {now}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Pages checked: {len(results)}")
    lines.append(f"- Links found: {total_links}")
    lines.append(f"- Links with issues or review notes: {len(issue_links)}")
    lines.append(f"- High-priority link issues: {len(high_links)}")
    lines.append(f"- Status check enabled: {'Yes' if args.check_status else 'No'}")
    lines.append("")

    if args.staging_domain:
        lines.append(f"- Staging domain: `{args.staging_domain}`")
    if args.live_domain:
        lines.append(f"- Live domain: `{args.live_domain}`")
    if args.allowed_domain:
        lines.append(f"- Allowed domains: `{', '.join(args.allowed_domain)}`")
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
        lines.append(f"- Links found: {len(page.links)}")
        lines.append("")

        page_issues = [
            link for link in page.links if link.issue or issue_level(link)
        ]

        if not page_issues:
            lines.append("No link issues detected on this page.")
            lines.append("")
            continue

        lines.append("| Priority | Category | Status | Link Text | Href | Issue |")
        lines.append("|---|---|---:|---|---|---|")

        for link in page_issues:
            priority = issue_level(link) or "Review"
            status_display = str(link.status_code) if link.status_code is not None else ""
            text_display = markdown_escape(link.link_text[:120] or "(no visible text)")
            href_display = markdown_escape(link.raw_href[:180])
            issue_display = markdown_escape(link.issue or link.error or "Review manually.")

            lines.append(
                f"| {priority} | {markdown_escape(link.category)} | {status_display} | "
                f"{text_display} | `{href_display}` | {issue_display} |"
            )

        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- This audit is read-only.")
    lines.append("- JavaScript-generated links may not be detected.")
    lines.append("- Visual QA and browser testing are still required.")
    lines.append("- Review high-priority items before launch.")
    lines.append("")

    return "\n".join(lines)


def build_allowed_domains(
    urls: Iterable[str],
    staging_domain: str | None,
    live_domain: str | None,
    extra_allowed: list[str] | None,
) -> set[str]:
    domains: set[str] = set()

    for url in urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc:
            domains.add(parsed.netloc.lower())

    if staging_domain:
        domains.add(staging_domain.lower())

    if live_domain:
        domains.add(live_domain.lower())

    if extra_allowed:
        for domain in extra_allowed:
            cleaned = domain.strip().lower()
            if cleaned:
                domains.add(cleaned)

    return domains


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only WordPress URL audit tool for staging/pre-launch QA."
    )

    parser.add_argument(
        "--pages",
        required=True,
        help="Path to a text file containing page URLs to audit.",
    )
    parser.add_argument(
        "--report",
        default="reports/url-audit.md",
        help="Path to the Markdown report file. Default: reports/url-audit.md",
    )
    parser.add_argument(
        "--check-status",
        action="store_true",
        help="Check HTTP status codes for extracted HTTP/HTTPS links.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Request timeout in seconds. Default: 20",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="Delay between link status checks in seconds. Default: 0.1",
    )
    parser.add_argument(
        "--staging-domain",
        default=None,
        help="Expected staging domain, e.g. staging.example.com",
    )
    parser.add_argument(
        "--live-domain",
        default=None,
        help="Live domain to detect during staging review, e.g. example.com",
    )
    parser.add_argument(
        "--allowed-domain",
        action="append",
        default=[],
        help="Additional allowed domain. Can be specified multiple times.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="Custom User-Agent header.",
    )

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    configure_console_stream(sys.stdout)
    configure_console_stream(sys.stderr)
    args = parse_args(argv)

    pages_path = Path(args.pages)
    report_path = Path(args.report)

    try:
        page_urls = read_page_list(pages_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not page_urls:
        print("ERROR: No page URLs found in page list.", file=sys.stderr)
        return 1

    allowed_domains = build_allowed_domains(
        urls=page_urls,
        staging_domain=args.staging_domain,
        live_domain=args.live_domain,
        extra_allowed=args.allowed_domain,
    )

    results: list[PageResult] = []

    print(f"Starting URL audit for {len(page_urls)} page(s)...")

    for index, page_url in enumerate(page_urls, start=1):
        print(f"[{index}/{len(page_urls)}] {page_url}")

        result = audit_page(
            page_url=page_url,
            timeout=args.timeout,
            user_agent=args.user_agent,
            check_status=args.check_status,
            staging_domain=args.staging_domain,
            live_domain=args.live_domain,
            allowed_domains=allowed_domains,
            delay=args.delay,
        )

        results.append(result)

    report = generate_report(results, args)

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
    except Exception as exc:
        print(f"ERROR: Failed to write report: {exc}", file=sys.stderr)
        return 1

    issue_count = sum(
        1
        for page in results
        for link in page.links
        if link.issue or issue_level(link)
    )

    print("")
    print(f"Report written: {report_path}")
    print(f"Pages checked: {len(results)}")
    print(f"Links with issues or review notes: {issue_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
