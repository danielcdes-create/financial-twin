"""Verify version synchronization across app.json, package.json, README.md, and local release artifacts."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_POINTER_SCRIPT = Path(__file__).resolve().with_name("release_pointer.py")
_POINTER_HEAD_RE = re.compile(
    r"^Release privado atual:\s*\*\*([^\s*]+)\s*/\s*versionCode\s+(\d+)\*\*"
)
_APK_LINK_RE = re.compile(r"mobile/artifacts/financial-twin-([^\s)]+)\.apk")


def check_version_sync(root: Path = REPO_ROOT) -> dict:
    root = Path(root).resolve()
    app_json_path = root / "mobile" / "app.json"
    pkg_json_path = root / "mobile" / "package.json"
    readme_path = root / "README.md"

    app_data = json.loads(app_json_path.read_text(encoding="utf-8-sig"))
    expo = app_data.get("expo") or {}
    app_version = str(expo.get("version") or "").strip()
    raw_code = (expo.get("android") or {}).get("versionCode")
    if not app_version or isinstance(raw_code, bool) or not isinstance(raw_code, int):
        raise ValueError("mobile/app.json must define expo.version and integer expo.android.versionCode")
    version_code = int(raw_code)

    pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8-sig"))
    pkg_version = str(pkg_data.get("version") or "").strip()
    if not pkg_version:
        raise ValueError("mobile/package.json must define a non-empty version")

    if app_version != pkg_version:
        raise ValueError(
            f"Version mismatch: mobile/app.json has '{app_version}' but mobile/package.json has '{pkg_version}'"
        )

    readme_text = readme_path.read_text(encoding="utf-8")
    pointer_lines = [
        line.rstrip("\r")
        for line in readme_text.splitlines()
        if line.startswith("Release privado atual:")
    ]
    if not pointer_lines:
        raise ValueError("README.md has no 'Release privado atual:' release pointer line")
    if len(pointer_lines) != 1:
        raise ValueError(
            f"README.md must have exactly one 'Release privado atual:' line, found {len(pointer_lines)}"
        )

    pointer_line = pointer_lines[0]
    match = _POINTER_HEAD_RE.match(pointer_line)
    if match is None:
        raise ValueError("README.md 'Release privado atual:' line does not match expected format")
    readme_version, readme_code = match.group(1), int(match.group(2))
    if (readme_version, readme_code) != (app_version, version_code):
        raise ValueError(
            f"README.md release pointer ({readme_version} / versionCode {readme_code}) "
            f"disagrees with mobile/app.json ({app_version} / versionCode {version_code})"
        )

    apk_link = _APK_LINK_RE.search(pointer_line)
    if apk_link is not None and apk_link.group(1) != app_version:
        raise ValueError(
            f"README.md APK link version '{apk_link.group(1)}' disagrees with version '{app_version}'"
        )

    manifest_path = root / "mobile" / "artifacts" / "financial-twin-arm64.manifest.json"
    apk_path = root / "mobile" / "artifacts" / "financial-twin-arm64.apk"
    manifest_verified = False
    if manifest_path.is_file() and apk_path.is_file():
        proc = subprocess.run(
            [
                sys.executable,
                str(RELEASE_POINTER_SCRIPT),
                "--manifest",
                str(manifest_path),
                "--app-config",
                str(app_json_path),
                "--readme",
                str(readme_path),
                "--check",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ValueError(f"Local release artifact check failed: {detail}")
        manifest_verified = True

    return {
        "version": app_version,
        "version_code": version_code,
        "manifest_verified": manifest_verified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    summary = check_version_sync(args.root)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
