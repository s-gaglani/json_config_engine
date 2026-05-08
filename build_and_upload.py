#!/usr/bin/env python3
"""
Cross-platform build and upload script for DWERP Common Utils package
Works on Windows, Linux, Debian, and macOS
"""

import subprocess
import sys
import shutil
from pathlib import Path
from decouple import config
from bump_version import bump_version


def run_command(cmd, cwd=None):
    """Run command and handle errors"""
    print(f"\nRunning: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True
    )

    if result.stdout:
        print("stdout:\n", result.stdout)

    if result.stderr:
        print("stderr:\n", result.stderr)

    if result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    return result


def clean_build():
    """Clean previous build artifacts"""
    print("Cleaning build artifacts...")

    # Remove standard build folders
    for folder in ["dist", "build"]:
        folder_path = Path(folder)
        if folder_path.exists():
            print(f"Removing {folder_path}")
            shutil.rmtree(folder_path, ignore_errors=True)

    # Remove *.egg-info folders/files
    for egg_info in Path(".").glob("*.egg-info"):
        print(f"Removing {egg_info}")
        if egg_info.is_dir():
            shutil.rmtree(egg_info, ignore_errors=True)
        elif egg_info.is_file():
            egg_info.unlink(missing_ok=True)


def build_package():
    """Build the package"""
    print("Updating build tools...")
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "setuptools", "wheel", "build"])

    print("Building package...")
    run_command([sys.executable, "-m", "build"])


def upload_to_nexus(feed_name, cert_file="nexus.crt"):
    """Upload to Nexus / private PyPI feed using Twine"""
    print("Uploading package to Nexus...")

    # Install twine if not available
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "twine"])

    # Find distribution files
    dist_files = list(Path("dist").glob("*"))
    if not dist_files:
        print("Error: No distribution files found in dist/")
        sys.exit(1)

    # Build twine command
    cmd = [
        sys.executable,
        "-m",
        "twine",
        "upload",
        "--repository",
        feed_name,
    ]

    cert_path = Path(cert_file)
    print(cert_file, ">>>>>>>>>cert file")
    if cert_path.exists():
        cmd.extend(["--cert", str(cert_path)])
    else:
        print(f"Warning: Certificate file '{cert_file}' not found. Uploading without --cert")

    cmd.extend([str(f) for f in dist_files])

    run_command(cmd)


def main():
    """Main build and upload process"""

    # ===== Defaults preserved here =====
    NEXUS_FEED_NAME = config("NEXUS_REPO", default="msbc")
    NEXUS_CERT_FILE = config("NEXUS_CERT_FILE", default="nexus.crt")
    AUTO_UPLOAD = config("AUTO_UPLOAD", default=True, cast=bool)

    print("Configuration:")
    print(f"  NEXUS_REPO      = {NEXUS_FEED_NAME}")
    print(f"  NEXUS_CERT_FILE = {NEXUS_CERT_FILE}")
    print(f"  AUTO_UPLOAD     = {AUTO_UPLOAD}")

    try:
        # Step 1: Clean previous builds
        clean_build()

        # Step 2: Build package
        build_package()

        # Step 3: Upload if enabled
        if AUTO_UPLOAD:
            upload_to_nexus(NEXUS_FEED_NAME, NEXUS_CERT_FILE)
        else:
            print("Skipping upload because AUTO_UPLOAD=False")
            print("To upload manually:")
            print(f'{sys.executable} -m twine upload --repository {NEXUS_FEED_NAME} dist/*')

        print("\nBuild completed successfully!")

    except Exception as e:
        print(f"\nBuild failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    bump_version()
    main()