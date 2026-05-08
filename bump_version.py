"""
Automatic version bumper for DWERP Common Utils.

Reads version from pyproject.toml, increments the patch number, and writes back.
Useful for CI/CD pipelines to automatically bump versions before package release.

Version Format:
    version = "MAJOR.MINOR.PATCH"  e.g., "1.0.0" → "1.0.1"

Only the PATCH number (rightmost) is incremented.

Usage:
    python bump_version.py

Example:
    Before: version = "1.0.0"
    After:  version = "1.0.1"

CI/CD Integration:
    # In pipeline (e.g., Azure Pipelines, GitHub Actions)
    - python bump_version.py
    - git commit -m "Bump version"
    - git push
    - python -m build  # Build wheel with new version
"""
import re

def bump_version(version:None=None):
    FILE = "pyproject.toml"

    with open(FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Find version = "x.y.z"
    match = re.search(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not match:
        raise ValueError("Version not found in pyproject.toml")

    major, minor, patch = map(int, match.groups())

    # Auto-bump patch
    patch += 1

    new_version =version if version else f"{major}.{minor}.{patch}"

    # Replace in file
    updated = re.sub(
        r'version\s*=\s*"\d+\.\d+\.\d+"',
        f'version = \"{new_version}\"',
        content
    )

    with open(FILE, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"Version bumped to: {new_version}")

 