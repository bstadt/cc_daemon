"""Terminal UI helpers for ClaudeConnect."""

from __future__ import annotations

import os
import shutil
import re
import struct
import sys
from typing import Iterable

# Terminal detection - Unix only
try:
    import fcntl
    import termios
    HAS_TERMINAL_DETECTION = True
except ImportError:
    HAS_TERMINAL_DETECTION = False

# PTY support (for persistent banner rendering)
try:
    import pty
    HAS_PTY = True
except ImportError:
    HAS_PTY = False

# ANSI color codes - matching Claude Code's aesthetic
# Slightly drier orange to match desired palette (RGB 215,119,87)
CORAL = '\033[38;2;215;119;87m'
CORAL_BG = '\033[48;2;215;119;87m'
LIME = '\033[38;5;114m'       # Muted lime green for friend Claude
LIME_BG = '\033[48;5;114m'    # Lime background
WHITE = '\033[97m'            # Bright white for main text
BOLD = '\033[1m'              # Bold text
DIM = '\033[2m'               # Dim for secondary text
BLACK = '\033[30m'            # Black for eyes
BLACK_BG = '\033[40m'         # Black background for transparency
YELLOW = '\033[38;5;228m'     # Soft yellow for sparkles
RESET = '\033[0m'
CLEAR = '\033[2J\033[H'       # Clear screen and move cursor to top
ORIGIN_MODE_ON = '\033[?6h'   # Origin mode: cursor positions relative to scroll region
ORIGIN_MODE_OFF = '\033[?6l'
RESET_SCROLL_REGION = '\033[r'


def get_cell_aspect_ratio() -> float | None:
    """Get the height/width ratio of a terminal cell."""
    if not HAS_TERMINAL_DETECTION:
        return None

    try:
        with open(os.ctermid(), 'r') as fd:
            packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack('HHHH', 0, 0, 0, 0))
            rows, cols, h_pixels, v_pixels = struct.unpack('HHHH', packed)

            if h_pixels == 0 or v_pixels == 0 or rows == 0 or cols == 0:
                return None  # Pixel dimensions not supported

            cell_width = h_pixels / cols
            cell_height = v_pixels / rows
            return cell_height / cell_width
    except (OSError, IOError):
        return None


def get_banner_style() -> str:
    """Determine which banner style to use."""
    banner_override = os.environ.get("CC_BANNER", "").lower()
    if banner_override in ("compact", "standard"):
        return banner_override

    term_program = os.environ.get("TERM_PROGRAM", "").lower()

    good_terminals = ["ghostty", "iterm.app", "alacritty", "kitty", "wezterm", "hyper"]
    if any(t in term_program for t in good_terminals):
        return "standard"

    problematic_terminals = ["apple_terminal"]
    if any(t in term_program for t in problematic_terminals):
        return "compact"

    ratio = get_cell_aspect_ratio()
    if ratio is not None and ratio >= 1.8:
        return "standard"

    return "compact"


def get_terminal_width(default: int = 80) -> int:
    """Return the current terminal width in columns."""
    return shutil.get_terminal_size((default, 24)).columns


def get_dashboard_width() -> int:
    """Return the target dashboard width (matches Claude Code box width)."""
    override = os.environ.get("CC_DASHBOARD_WIDTH", "").strip()
    if override.isdigit():
        return max(40, int(override))
    terminal_width = get_terminal_width()
    target = 80
    return max(40, min(target, terminal_width - 2))


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _pad_visible(text: str, width: int) -> str:
    visible = len(_strip_ansi(text))
    if visible >= width:
        return text
    return text + (" " * (width - visible))


def should_use_persistent_banner() -> bool:
    """Check if persistent banner rendering is enabled and supported."""
    override = os.environ.get("CC_PERSIST_BANNER", "").lower()
    if override in ("0", "false", "no"):
        return False
    if not HAS_PTY or not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    if override in ("1", "true", "yes"):
        return True
    return True


def get_double_banner_lines(email: str) -> list[str]:
    """Return the double-Claude banner lines without surrounding spacing."""
    style = get_banner_style()
    if style == "compact":
        double_creature = f"""
{CORAL}▗{CORAL_BG} {RESET}{CORAL_BG}{BLACK}▗{RESET}{CORAL_BG}   {RESET}{CORAL_BG}{BLACK}▖{RESET}{CORAL_BG} {RESET}{CORAL}▖{RESET} {YELLOW}✱{RESET} {LIME}▗{LIME_BG} {RESET}{LIME_BG}{BLACK}▗{RESET}{LIME_BG}   {RESET}{LIME_BG}{BLACK}▖{RESET}{LIME_BG} {RESET}{LIME}▖{RESET}
 {CORAL_BG}       {RESET}     {LIME_BG}       {RESET}
{CORAL}  ▘▘ ▝▝    {RESET} {LIME}  ▘▘ ▝▝    {RESET}
"""
        return double_creature.strip("\n").splitlines()

    return [
        f" {CORAL}▐{BLACK_BG}▛███▜▌{RESET} {YELLOW}✱{RESET} {LIME}▐{BLACK_BG}▛███▜{RESET}{LIME}▌{RESET}   {WHITE}{BOLD}Claude Connect{RESET}",
        f"{CORAL}▝▜█████▛▘{RESET} {LIME}▝▜█████▛▘{RESET}  {DIM}{email}{RESET}",
        f"  {CORAL}▘▘ ▝▝{RESET}     {LIME}▘▘ ▝▝{RESET}",
    ]


def build_banner_box_lines(email: str, peer_name: str | None, width: int) -> list[str]:
    """Build the bordered banner box with the double-Claude art."""
    lines = list(get_double_banner_lines(email))
    if get_banner_style() == "compact":
        lines.append(f"{WHITE}{BOLD}Claude Connect{RESET}")
        lines.append(f"{DIM}{email}{RESET}")
    if peer_name:
        lines.append("")
        lines.append(f"Interactive session with {LIME}{peer_name}{RESET}")

    max_art_width = max(len(_strip_ansi(line)) for line in lines) if lines else 0
    width = max(width, max_art_width + 4, 40)
    inner_width = width - 2
    content_width = inner_width - 2

    title = "Claude Connect"
    title_text = f" {title} "
    title_len = len(title_text)
    if title_len > inner_width:
        title_text = f" {title[: max(0, inner_width - 2)]} "
        title_len = len(title_text)
    dashes = max(0, inner_width - title_len)
    top_line = f"┌{title_text}" + ("─" * dashes) + "┐"
    boxed = [f"{CORAL}{top_line}{RESET}"]
    boxed.append(f"{CORAL}│{RESET} " + (" " * content_width) + f" {CORAL}│{RESET}")
    for line in lines:
        padded = _pad_visible(line, content_width)
        boxed.append(f"{CORAL}│{RESET} {padded} {CORAL}│{RESET}")
    boxed.append(f"{CORAL}└" + ("─" * inner_width) + f"┘{RESET}")
    return boxed


def get_persistent_banner_lines(
    email: str,
    peer_name: str | None = None,
    extra_lines: Iterable[str] | None = None,
    header_lines: Iterable[str] | None = None,
) -> list[str]:
    """Return banner lines for persistent header mode."""
    if header_lines is None:
        lines = get_double_banner_lines(email)
        if peer_name:
            lines.append(f"  {WHITE}{BOLD}Interactive session with {LIME}{peer_name}{RESET}")
    else:
        lines = list(header_lines)
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)
    lines.append("")
    return lines


def _build_persistent_banner_bytes(banner_lines: list[str]) -> bytes:
    rows = shutil.get_terminal_size((80, 24)).lines
    top = len(banner_lines) + 1
    header = "\n".join(banner_lines)
    if header and not header.endswith("\n"):
        header += "\n"
    scroll_seq = ""
    origin_seq = ""
    cursor_seq = ""
    if rows > top:
        scroll_seq = f"\033[{top};{rows}r"
        origin_seq = ORIGIN_MODE_ON
        cursor_seq = "\033[H"
    return f"\033[H{header}{RESET}{scroll_seq}{origin_seq}{cursor_seq}".encode()


def _write_stdout_bytes(data: bytes) -> None:
    try:
        os.write(sys.stdout.fileno(), data)
    except OSError:
        sys.stdout.write(data.decode(errors="ignore"))
        sys.stdout.flush()


def render_persistent_banner(banner_lines: list[str]) -> None:
    """Render banner and set scroll region/origin mode for persistent header."""
    _write_stdout_bytes(b"\033[2J")
    _write_stdout_bytes(_build_persistent_banner_bytes(banner_lines))


def reset_persistent_banner() -> None:
    """Reset terminal scroll region/origin mode after persistent header."""
    _write_stdout_bytes(f"{ORIGIN_MODE_OFF}{RESET_SCROLL_REGION}{RESET}".encode())


class PersistentBannerFilter:
    """Filter terminal output to re-render a persistent banner on clear-screen redraws."""

    def __init__(self, banner_lines: list[str]) -> None:
        self.banner_lines = banner_lines
        self._pending = b""

    def filter_output(self, data: bytes) -> bytes:
        if not data:
            return data
        data = self._pending + data
        self._pending = b""

        pending = b""
        for suffix in (b"\x1b[2", b"\x1b[", b"\x1b"):
            if data.endswith(suffix):
                pending = suffix
                data = data[:-len(suffix)]
                break
        self._pending = pending

        out = bytearray()
        i = 0
        while i < len(data):
            if data.startswith(b"\x1b[2J", i):
                out.extend(b"\x1b[2J")
                out.extend(_build_persistent_banner_bytes(self.banner_lines))
                i += 4
                continue
            out.append(data[i])
            i += 1
        return bytes(out)


def run_claude_with_persistent_banner(
    claude_args: list[str],
    context_dir: os.PathLike[str] | str,
    banner_lines: list[str],
    render_initial: bool = False,
) -> None:
    """Run Claude in a PTY and keep a persistent banner pinned to the top."""
    if not HAS_PTY:
        raise RuntimeError("PTY support is unavailable on this platform.")
    banner_filter = PersistentBannerFilter(banner_lines)
    if render_initial:
        render_persistent_banner(banner_lines)

    def master_read(fd: int) -> bytes:
        data = os.read(fd, 1024)
        if not data:
            return data
        return banner_filter.filter_output(data)

    cwd = os.getcwd()
    try:
        os.chdir(str(context_dir))
        pty.spawn(claude_args, master_read)
    finally:
        os.chdir(cwd)
