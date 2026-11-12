"""Syntax / sanity checks for the start & stop commands.

PowerShell: parsed with the real PowerShell parser (syntax errors are exact).
CMD: no real parser exists, so these are heuristics (quotes, parentheses, goto labels,
unknown commands, paths that do not exist).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

CREATE_NO_WINDOW = 0x08000000


@dataclass
class Issue:
    level: str   # "error" | "warning"
    line: int    # 1-based, 0 = whole script
    message: str

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        icon = "✖" if self.level == "error" else "⚠"
        return f"{icon} {where}{self.message}"


CMD_BUILTINS = {
    "assoc", "break", "call", "cd", "chdir", "cls", "color", "copy", "date", "del", "dir", "echo",
    "endlocal", "erase", "exit", "for", "ftype", "goto", "if", "md", "mkdir", "mklink", "move", "path",
    "pause", "popd", "prompt", "pushd", "rd", "rem", "ren", "rename", "rmdir", "set", "setlocal",
    "shift", "start", "time", "title", "type", "ver", "verify", "vol",
}

PATH_RE = re.compile(r'(?<![\w%])([A-Za-z]:\\[^"<>|?*\r\n%]*[^"<>|?*\s%,;)])')


def _strip_quotes(s: str) -> str:
    return re.sub(r'"[^"]*"', '""', s)


def _check_paths(lines: list[str], issues: list[Issue]) -> None:
    seen = set()
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip().lower()
        if stripped.startswith(("rem ", "::")):
            continue
        # take quoted segments first; otherwise unquoted drive paths up to whitespace
        candidates = re.findall(r'"([A-Za-z]:\\[^"]*)"', raw)
        candidates += re.findall(r'(?<![\w"%])([A-Za-z]:\\[^\s"<>|]*)', _strip_quoted_text(raw))
        for c in candidates:
            c = c.rstrip("\\ ,;)")
            if "%" in c or "*" in c or "?" in c or c in seen:
                continue
            seen.add(c)
            if not os.path.exists(c):
                # a path being created (md / mkdir / output redirect) may legitimately not exist yet
                if re.match(r"\s*(md|mkdir|echo|set)\b", raw, re.I) or ">" in raw:
                    continue
                issues.append(Issue("warning", i, f"path does not exist: {c}"))


def _strip_quoted_text(s: str) -> str:
    return re.sub(r'"[^"]*"', " ", s)


def check_cmd(script: str) -> list[Issue]:
    issues: list[Issue] = []
    lines = script.splitlines()
    labels = {m.group(1).lower() for l in lines if (m := re.match(r"\s*:([^\s:][^\s]*)", l))}
    depth = 0
    depth_open_line = 0
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("::") or re.match(r"@?rem(\s|$)", line, re.I):
            continue
        if line.startswith(":"):
            continue
        body = line.lstrip("@").strip()

        if body.count('"') % 2:
            issues.append(Issue("error", i, "unbalanced double quote (\")"))

        # parentheses (ignoring quoted text, escaped ^( ^) and echo lines)
        if not re.match(r"echo\b", body, re.I):
            plain = re.sub(r"\^.", "", _strip_quotes(body))
            for ch in plain:
                if ch == "(":
                    if depth == 0:
                        depth_open_line = i
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        issues.append(Issue("error", i, "closing ) without a matching ("))
                        depth = 0

        m = re.match(r"(?:if|.*\bif)\b", body, re.I)
        if re.fullmatch(r"if(\s+/i)?(\s+not)?", body, re.I):
            issues.append(Issue("error", i, "'if' has no condition"))
        if re.match(r"for\b", body, re.I) and not re.search(r"\bdo\b", body, re.I):
            issues.append(Issue("error", i, "'for' without 'do'"))

        # goto / call :label must exist
        for g in re.finditer(r"\b(?:goto|call)\s+:?([A-Za-z0-9_.\-]+)", body, re.I):
            kind = g.group(0).split()[0].lower()
            tgt = g.group(1).lower()
            if kind == "goto" and tgt != "eof" and tgt not in labels:
                issues.append(Issue("error", i, f"goto target ':{g.group(1)}' is not defined"))
            if kind == "call" and body[g.start(1) - 1] == ":" and tgt not in labels:
                issues.append(Issue("error", i, f"call target ':{g.group(1)}' is not defined"))

        # unknown command (first word of a plain line)
        first = re.match(r'(?:\(|\s)*("[^"]+"|[^\s&|<>()]+)', body)
        if first and not body.startswith((")", "(")):
            word = first.group(1).strip('"')
            low = word.lower()
            skip = (low in CMD_BUILTINS or low.startswith(("%", "!")) or "=" in low
                    or low.startswith(("if", "for")) and low in ("if", "for"))
            if not skip:
                found = (shutil.which(word) or os.path.exists(word)
                         or os.path.exists(os.path.expandvars(word)))
                if not found:
                    issues.append(Issue("warning", i, f"'{word}' is not a built-in command and was not found"))

    if depth > 0:
        issues.append(Issue("error", depth_open_line, "( is never closed"))
    _check_paths(lines, issues)
    return sorted(issues, key=lambda x: (x.line, x.level))


_PS_PARSE = r"""
$errs = $null; $tokens = $null
$src = [Console]::In.ReadToEnd()
[void][System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$tokens, [ref]$errs)
$out = @($errs | ForEach-Object { @{ line = $_.Extent.StartLineNumber; msg = $_.Message } })
ConvertTo-Json -InputObject $out -Compress
"""


def check_powershell(script: str) -> list[Issue]:
    issues: list[Issue] = []
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return [Issue("warning", 0, "PowerShell not found, cannot check syntax")]
    try:
        r = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", _PS_PARSE],
            input=script, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(r.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        for e in data:
            issues.append(Issue("error", int(e["line"]), str(e["msg"])))
    except (subprocess.SubprocessError, ValueError, OSError) as ex:
        issues.append(Issue("warning", 0, f"syntax check failed to run: {ex}"))
    _check_paths(script.splitlines(), issues)
    return sorted(issues, key=lambda x: (x.line, x.level))


def check(script: str, shell: str) -> list[Issue]:
    if not script.strip():
        return []
    return check_powershell(script) if shell == "powershell" else check_cmd(script)
