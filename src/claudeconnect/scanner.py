"""Sensitive information scanner for ClaudeConnect.

Scans context directories for PII, credentials, financial data, and other
sensitive information before first sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class SensitiveMatch:
    """A single sensitive pattern match."""
    file_path: Path
    line_number: int
    category: str
    pattern_name: str
    severity: str  # "low", "medium", "high"
    matched_text: str
    context_line: str


@dataclass
class ScanReport:
    """Results of a sensitive info scan."""
    matches: list[SensitiveMatch] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def has_issues(self) -> bool:
        return len(self.matches) > 0

    @property
    def has_high_severity(self) -> bool:
        return any(m.severity == "high" for m in self.matches)

    @property
    def has_medium_or_higher(self) -> bool:
        return any(m.severity in ("high", "medium") for m in self.matches)

    def by_file(self) -> dict[Path, list[SensitiveMatch]]:
        """Group matches by file."""
        result: dict[Path, list[SensitiveMatch]] = {}
        for match in self.matches:
            if match.file_path not in result:
                result[match.file_path] = []
            result[match.file_path].append(match)
        return result

    def by_severity(self) -> dict[str, list[SensitiveMatch]]:
        """Group matches by severity."""
        result: dict[str, list[SensitiveMatch]] = {"high": [], "medium": [], "low": []}
        for match in self.matches:
            result[match.severity].append(match)
        return result

    def format_report(self, context_dir: Path | None = None) -> str:
        """Format report for terminal display."""
        if not self.matches:
            return ""

        lines = []
        by_severity = self.by_severity()

        # Summary header
        high_count = len(by_severity["high"])
        medium_count = len(by_severity["medium"])
        low_count = len(by_severity["low"])

        severity_parts = []
        if high_count:
            severity_parts.append(f"{high_count} high")
        if medium_count:
            severity_parts.append(f"{medium_count} medium")
        if low_count:
            severity_parts.append(f"{low_count} low")

        lines.append(f"Found {len(self.matches)} potential issues ({', '.join(severity_parts)})")
        lines.append("")

        # Group by file
        for file_path, file_matches in sorted(self.by_file().items()):
            # Show relative path if context_dir provided
            display_path = file_path
            if context_dir:
                try:
                    display_path = file_path.relative_to(context_dir)
                except ValueError:
                    pass

            lines.append(f"[{display_path}]")
            for match in sorted(file_matches, key=lambda m: m.line_number):
                severity_icon = {"high": "!", "medium": "*", "low": "-"}[match.severity]
                # Truncate matched text for display
                display_text = match.matched_text
                if len(display_text) > 40:
                    display_text = display_text[:37] + "..."
                lines.append(f"  {severity_icon} Line {match.line_number}: {match.pattern_name} - {display_text}")
            lines.append("")

        return "\n".join(lines)


# Pattern definitions
# Each pattern: (regex, severity, description)
SENSITIVE_PATTERNS: dict[str, dict[str, tuple[str, str, str]]] = {
    "Credentials": {
        "API Key (Generic)": (
            r"(?i)(api[_-]?key|apikey|api_secret)\s*[:=]\s*['\"]?[\w\-]{20,}",
            "high",
            "API key or secret"
        ),
        "AWS Access Key": (
            r"AKIA[0-9A-Z]{16}",
            "high",
            "AWS access key ID"
        ),
        "AWS Secret Key": (
            r"(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['\"]?[\w/+=]{40}",
            "high",
            "AWS secret access key"
        ),
        "Private Key": (
            r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----",
            "high",
            "Private key header"
        ),
        "JWT Token": (
            r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
            "high",
            "JWT token"
        ),
        "Generic Secret": (
            r"(?i)(secret|password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
            "medium",
            "Password or secret value"
        ),
        "Bearer Token": (
            r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}",
            "high",
            "Bearer authentication token"
        ),
        "OpenAI Key": (
            r"sk-[a-zA-Z0-9]{20,}",
            "high",
            "OpenAI API key"
        ),
        "Anthropic Key": (
            r"sk-ant-[a-zA-Z0-9\-]{20,}",
            "high",
            "Anthropic API key"
        ),
        "GitHub Token": (
            r"gh[pousr]_[A-Za-z0-9_]{36,}",
            "high",
            "GitHub personal access token"
        ),
    },
    "PII": {
        "SSN": (
            r"\b\d{3}-\d{2}-\d{4}\b",
            "high",
            "Social Security Number pattern"
        ),
        "Credit Card": (
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b",
            "high",
            "Credit card number"
        ),
        "Phone Number": (
            r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            "low",
            "Phone number"
        ),
        "Email Address": (
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "low",
            "Email address"
        ),
    },
    "Financial": {
        "Bank Account": (
            r"(?i)(account|routing)\s*(?:number|#|num)?[:=\s]+\d{8,17}",
            "high",
            "Bank account or routing number"
        ),
        "IBAN": (
            r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b",
            "high",
            "International Bank Account Number"
        ),
    },
    "Infrastructure": {
        "IP Address": (
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
            "low",
            "IP address"
        ),
        "Database URL": (
            r"(?i)(postgres|mysql|mongodb|redis)://[^\s]+",
            "medium",
            "Database connection string"
        ),
    },
}

# Files/directories to skip
SKIP_PATTERNS = {
    ".svn",
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}

# File extensions to scan
SCAN_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".env", ".conf", ".cfg"}


class SensitiveInfoScanner:
    """Scans directories for sensitive information."""

    def __init__(self, context_dir: Path, extensions: set[str] | None = None):
        """
        Initialize scanner.

        Args:
            context_dir: Directory to scan
            extensions: File extensions to scan (default: markdown + config files)
        """
        self.context_dir = context_dir
        self.extensions = extensions or SCAN_EXTENSIONS
        self._compiled_patterns: dict[str, dict[str, tuple[re.Pattern, str, str]]] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile regex patterns for efficiency."""
        for category, patterns in SENSITIVE_PATTERNS.items():
            self._compiled_patterns[category] = {}
            for name, (pattern, severity, desc) in patterns.items():
                try:
                    self._compiled_patterns[category][name] = (
                        re.compile(pattern),
                        severity,
                        desc,
                    )
                except re.error:
                    # Skip invalid patterns
                    pass

    def _should_scan_file(self, path: Path) -> bool:
        """Check if file should be scanned."""
        # Skip hidden files (except .env)
        if path.name.startswith(".") and path.name != ".env":
            return False

        # Check extension
        return path.suffix.lower() in self.extensions

    def _should_skip_dir(self, path: Path) -> bool:
        """Check if directory should be skipped."""
        return path.name in SKIP_PATTERNS

    def _iter_files(self) -> Iterator[Path]:
        """Iterate over files to scan."""
        for path in self.context_dir.rglob("*"):
            if path.is_file():
                # Check if any parent should be skipped
                skip = False
                for parent in path.parents:
                    if parent == self.context_dir:
                        break
                    if self._should_skip_dir(parent):
                        skip = True
                        break

                if not skip and self._should_scan_file(path):
                    yield path

    def _scan_line(self, line: str, line_num: int, file_path: Path) -> Iterator[SensitiveMatch]:
        """Scan a single line for sensitive patterns."""
        for category, patterns in self._compiled_patterns.items():
            for pattern_name, (regex, severity, desc) in patterns.items():
                for match in regex.finditer(line):
                    matched_text = match.group(0)
                    # Mask sensitive data for display
                    if severity == "high" and len(matched_text) > 8:
                        display_text = matched_text[:4] + "*" * (len(matched_text) - 8) + matched_text[-4:]
                    else:
                        display_text = matched_text

                    yield SensitiveMatch(
                        file_path=file_path,
                        line_number=line_num,
                        category=category,
                        pattern_name=pattern_name,
                        severity=severity,
                        matched_text=display_text,
                        context_line=line.strip()[:100],
                    )

    def _scan_file(self, path: Path) -> Iterator[SensitiveMatch]:
        """Scan a single file for sensitive patterns."""
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for line_num, line in enumerate(content.splitlines(), 1):
                yield from self._scan_line(line, line_num, path)
        except (OSError, UnicodeDecodeError):
            # Skip files we can't read
            pass

    def scan(self) -> ScanReport:
        """
        Scan the context directory for sensitive information.

        Returns:
            ScanReport with all matches found
        """
        report = ScanReport()

        for file_path in self._iter_files():
            report.files_scanned += 1
            for match in self._scan_file(file_path):
                report.matches.append(match)

        return report

    def scan_markdown_only(self) -> ScanReport:
        """Scan only markdown files (what actually gets synced)."""
        original_extensions = self.extensions
        self.extensions = {".md"}
        try:
            return self.scan()
        finally:
            self.extensions = original_extensions


def scan_directory(context_dir: Path, markdown_only: bool = True) -> ScanReport:
    """
    Convenience function to scan a directory.

    Args:
        context_dir: Directory to scan
        markdown_only: If True, only scan .md files (default)

    Returns:
        ScanReport with results
    """
    scanner = SensitiveInfoScanner(context_dir)
    if markdown_only:
        return scanner.scan_markdown_only()
    return scanner.scan()
