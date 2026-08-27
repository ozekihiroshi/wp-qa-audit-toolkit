# wp-qa-audit-toolkit

A practical read-only toolkit for WordPress pre-launch QA, staging-site review, link checking, and content cleanup planning.

This toolkit is designed for WordPress sites that are close to launch but still have QA issues such as broken links, incorrect internal URLs, empty buttons, inconsistent page links, staging/live URL mix-ups, and content cleanup tasks.

The goal is not to automatically modify a WordPress site.  
The goal is to help reviewers and developers identify problems safely before making controlled changes.

## Purpose

Many WordPress QA tasks are handled manually through spreadsheets, screenshots, and staging-site reviews. This often leads to missed links, inconsistent page references, repeated issues, and accidental regressions.

This toolkit helps with:

- Pre-launch WordPress QA
- Staging-site link review
- Broken or suspicious URL detection
- Internal link consistency checks
- Live-site vs staging-site link checks
- Empty link and placeholder link detection
- QA handover and status reporting
- Safer cleanup planning before launch

## Where to Run It

Normally run this toolkit from your own workstation, WSL, or Linux environment. The scripts read local page-list and policy files, fetch the listed public or staging pages over HTTP/HTTPS, and write Markdown reports locally. They do not require WordPress Admin, SSH, or target-server filesystem access.

The chosen execution environment must be able to reach the pages. Basic authentication, IP restrictions, a VPN, or bot protection may therefore require an authorized network viewpoint. The scripts still inspect returned HTML only; they do not execute JavaScript or make changes.

## Basic Usage

Clone or download the repository, open a terminal in a Python environment, and run the audit from the repository directory. On Windows, use WSL or another Python 3 environment.

First edit `examples/qa-pages.txt` so it contains the public or staging pages you are authorized to review, then run:

```bash
cd /path/to/wp-qa-audit-toolkit
python3 scripts/wp-url-audit.py \
  --pages examples/qa-pages.txt \
  --report reports/url-audit.md
```

The script fetches the listed pages and writes the report locally. It does not require WordPress Admin, SSH, target-server filesystem access, or root privileges.

Commands under `tests/` verify this toolkit itself; they are not a QA audit of a client site.

## Current Tools

### `scripts/wp-url-audit.py`

A read-only URL audit script for WordPress staging or production pages.

It can:

- Read a list of page URLs from a text file
- Fetch each page
- Extract links from `<a href="...">`
- Classify links as internal, external, anchor, email, telephone, JavaScript, empty, or suspicious
- Optionally check HTTP status codes
- Detect links pointing to the live site when reviewing a staging site
- Detect links pointing to a different domain
- Generate a Markdown report

## Usage

Create a page list file:

```text
https://staging.example.com/
https://staging.example.com/what-we-treat/
https://staging.example.com/faq/
```

Run a basic audit:

```bash
python3 scripts/wp-url-audit.py \
  --pages examples/qa-pages.txt \
  --report reports/url-audit.md
```

Run an audit with HTTP status checks:

```bash
python3 scripts/wp-url-audit.py \
  --pages examples/qa-pages.txt \
  --check-status \
  --report reports/url-audit.md
```

Detect live-site links during staging review:

```bash
python3 scripts/wp-url-audit.py \
  --pages examples/qa-pages.txt \
  --staging-domain staging.example.com \
  --live-domain example.com \
  --check-status \
  --report reports/url-audit.md
```
### `scripts/wp-text-policy-audit.py`

A read-only text policy audit script for WordPress staging or production pages.

It can:

- Read a list of page URLs from a text file
- Read a simple text policy file
- Fetch each page
- Extract visible text from HTML
- Detect old brand names, outdated terms, typo patterns, and wording consistency issues
- Generate a Markdown report for manual QA review


## Safety

This toolkit is read-only.

It does not:

- Modify WordPress content
- Change theme files
- Change plugins
- Update the database
- Log in to WordPress
- Delete files
- Change server settings

It only fetches public page HTML and reports what it finds.

## Recommended QA Workflow

1. Confirm staging URL and target launch domain.
2. Create a list of important pages.
3. Run the URL audit.
4. Review suspicious links.
5. Fix issues manually in WordPress, the page builder, menus, theme options, or templates.
6. Re-run the audit.
7. Keep the final report as launch documentation.

## Typical Use Cases

- A WordPress site has many pre-launch QA comments.
- A previous developer made fixes but introduced new issues.
- Buttons scroll to the footer instead of opening the correct page.
- Menus and body links point to different destinations.
- Staging pages still link to the current live site.
- FAQ links point to old URLs.
- A client needs a clear QA status report before launch.

## Limitations

This script does not execute JavaScript.  
Links created dynamically after page load may not be detected.

This script does not replace browser-based visual QA.  
It should be used together with manual review.

This script does not automatically fix issues.  
It is designed to reduce manual checking errors and support safer QA work.

## Requirements and Toolkit Verification

- Python 3.8 or later
- No third-party package is required by the current URL and text-policy audits
- Reports are written as UTF-8 Markdown
- Windows console output uses safe replacement for characters outside the active console encoding; report files retain UTF-8 text

Run the regression tests:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

This test command uses synthetic test cases. It does not fetch or audit a client site.

## Repository boundary

This repository remains separate from `wp-rescue-toolkit` because its scripts inspect rendered public-page links and text policies. `wp-rescue-toolkit` handles WordPress HTTP/header/endpoint baselines and authenticated/filesystem/database routes. Use both for pre-launch or migration validation when the scope requires both layers.

Possible future additions include a QA list normalizer, FAQ structure audit, image alt-text audit, and pre-launch checklist generator.

## License

MIT License.