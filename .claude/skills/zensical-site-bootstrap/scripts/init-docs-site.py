#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Initialize an English Zensical site without installing or publishing anything."""

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent.parent / "assets"
VERSION = "0.0.73"


def safe_path(root, relative):
    path = root
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"refusing symlink: {path}")
    return path


def initialize(args):
    target = Path(args.target_dir).expanduser()
    if target.is_symlink() or not target.is_dir():
        raise ValueError("--target-dir must be an existing non-symlink directory")
    root = target.resolve()
    url = urlsplit(args.site_url)
    if (url.scheme not in {"https", "http"} or not url.netloc or url.username
            or url.password or url.query or url.fragment or not args.site_url.endswith("/")):
        raise ValueError("--site-url must be an HTTP(S) URL ending in /, without credentials/query/fragment")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo_slug):
        raise ValueError("--repo-slug must be owner/repo")
    if not args.site_name.strip() or "\n" in args.site_name:
        raise ValueError("--site-name must be a nonempty single line")
    for name in ("mkdocs.yml", "mkdocs.yaml", "zensical.toml"):
        path = safe_path(root, name)
        if path.exists():
            raise ValueError(f"existing {name}; maintain it explicitly instead of bootstrapping over it")

    docs = safe_path(root, "docs")
    if docs.exists() and not docs.is_dir():
        raise ValueError("docs exists but is not a directory")
    existing = list(docs.rglob("*")) if docs.exists() else []
    if any(path.is_symlink() for path in existing):
        raise ValueError("docs contains symlinks; select a real, curated source tree first")
    if existing and args.existing != "keep":
        raise ValueError("docs is nonempty; review its publishable contents, then use --existing keep")
    if existing and not (docs / "index.md").is_file():
        raise ValueError("existing docs needs an index.md; no content will be generated over it")

    values = {
        "SITE_NAME": json.dumps(args.site_name, ensure_ascii=False),
        "SITE_URL": json.dumps(args.site_url),
        "REPO_URL": json.dumps(f"https://github.com/{args.repo_slug}"),
    }
    config = re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: values[match[1]],
                    (ASSETS / "zensical.toml.template").read_text())
    tomllib.loads(config)
    files = {
        "zensical.toml": config,
        "scripts/check-docs-site.py": (ASSETS / "check-docs-site.py").read_text(),
    }
    if not existing:
        files["docs/index.md"] = (ASSETS / "index.md.template").read_text().replace(
            "{{SITE_NAME}}", args.site_name)
    if args.ci:
        files[".github/workflows/docs-check.yml"] = (ASSETS / "docs-check.yml").read_text()

    pyproject = safe_path(root, "pyproject.toml")
    if pyproject.exists():
        tomllib.loads(pyproject.read_text())
    else:
        files["pyproject.toml"] = (
            '[project]\nname = "project-docs"\nversion = "0.0.0"\n'
            'requires-python = ">=3.11"\ndependencies = []\n\n'
            f'[dependency-groups]\ndocs = ["zensical=={VERSION}"]\n'
            '\n[tool.uv]\npackage = false\n'
        )
    for relative in files:
        destination = safe_path(root, relative)
        if destination.exists():
            raise ValueError(f"would overwrite {relative}; resolve the conflict first")
        if any(parent.exists() and not parent.is_dir()
               for parent in destination.parents if parent != root.parent):
            raise ValueError(f"non-directory parent for {relative}")

    ignore = safe_path(root, ".gitignore")
    original_ignore = ignore.read_text() if ignore.exists() else ""
    ignored = original_ignore.splitlines()
    additions = [line for line in ("/site/", "/.cache/", "/.venv/") if line not in ignored]
    updated_ignore = original_ignore
    if additions:
        if updated_ignore and not updated_ignore.endswith("\n"):
            updated_ignore += "\n"
        updated_ignore += "\n".join(additions) + "\n"

    if not args.dry_run:
        for relative, content in files.items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(content)
        if additions:
            ignore.write_text(updated_ignore, encoding="utf-8")

    return {
        "status": "planned" if args.dry_run else "created",
        "target": str(root),
        "files": list(files),
        "preserved_existing_docs": bool(existing),
        "gitignore_additions": additions,
        "next_steps": [
            f"uv add --group docs 'zensical=={VERSION}'" if pyproject.exists()
            and "pyproject.toml" not in files else "uv lock",
            "uv sync --locked --only-group docs",
            "uv run --no-sync zensical build --strict",
            "uv run --no-sync python scripts/check-docs-site.py",
        ],
        "deployed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", required=True, help="Existing project root (never inferred from CWD)")
    parser.add_argument("--site-name", required=True)
    parser.add_argument("--site-url", required=True, help="Future site base URL including /project/ and trailing /")
    parser.add_argument("--repo-slug", required=True, help="GitHub owner/repo")
    parser.add_argument("--existing", choices=["keep"], help="Preserve an explicitly reviewed existing docs tree")
    parser.add_argument("--ci", action="store_true", help="Add build-only Actions workflow; never deploy")
    parser.add_argument("--dry-run", action="store_true", help="Report exact planned files without writing")
    args = parser.parse_args()
    try:
        result = initialize(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(json.dumps({"status": "blocked", "deployed": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
