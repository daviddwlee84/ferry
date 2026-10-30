#!/usr/bin/env python3
"""Check the English all-pages artifact contract; never build or publish the site."""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote, urljoin, urlsplit


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.copy_urls = []
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-md-copy-url" in attrs:
            self.copy_urls.append(attrs["data-md-copy-url"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "img" and attrs.get("src"):
            self.links.append(attrs["src"])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check(root):
    project = tomllib.loads((root / "zensical.toml").read_text())["project"]
    require(project.get("docs_dir", "docs") == "docs" and project.get("site_dir", "site") == "site",
            "this checker expects docs/ and site/; review it before changing output layout")
    require(project.get("theme", {}).get("language") == "en", "this pilot validates English content only")
    require(not project.get("hooks"), "MkDocs hooks are not supported by this pilot")
    plugins = project.get("plugins", {})
    require(not (set(plugins) - {"search", "llmstxt"}),
            "additional plugins need an explicit compatibility and artifact review")
    require(plugins.get("llmstxt", {}).get("full_output") == "llms-full.txt",
            "expected llmstxt.full_output = llms-full.txt")
    base = project["site_url"]
    require(base.endswith("/"), "site_url must end in /")
    origin = urlsplit(base)
    docs, site = root / "docs", root / "site"
    for directory in (docs, site):
        require(directory.is_dir() and not directory.is_symlink(), f"missing or symlinked {directory.name}/")
        require(not any(path.is_symlink() for path in directory.rglob("*")),
                f"{directory.name}/ contains symlinks")
    sources = sorted(docs.rglob("*.md"))
    require(sources and (docs / "index.md") in sources, "expected docs/index.md and Markdown pages")

    def local_file(url):
        parsed = urlsplit(url)
        require((parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc),
                f"artifact URL uses a different origin: {url}")
        require(parsed.path.startswith(origin.path), f"artifact URL drops the project subpath: {url}")
        relative = unquote(parsed.path[len(origin.path):])
        path = (site / relative).resolve()
        require(path.is_relative_to(site.resolve()), f"artifact URL escapes site/: {url}")
        if parsed.path.endswith("/"):
            path /= "index.html"
        require(path.is_file(), f"missing artifact for {url}")
        return path

    index = (site / "llms.txt").read_text()
    full = (site / "llms-full.txt").read_text()
    links = re.findall(r"^\s*-\s+\[.*?\]\(<?([^)>\s]+)>?\)", index, re.MULTILINE)
    exported = [local_file(link) for link in links]
    require(len(exported) == len(set(exported)), "duplicate pages in llms.txt")
    expected = set()
    for source in sources:
        relative = source.relative_to(docs)
        directory = relative.parent if relative.name == "index.md" else relative.with_suffix("")
        markdown = site / directory / "index.md"
        html = site / directory / "index.html"
        expected.add(markdown.resolve())
        require(markdown.is_file() and html.is_file(), f"missing HTML/Markdown pair for {relative}")
        content = markdown.read_text().strip()
        require(content and content in full, f"full output omits content from {relative}")
        page = Page(html.read_text())
        html_url = urljoin(base, html.relative_to(site).as_posix())
        require(page.copy_urls, f"missing Copy as Markdown button for {relative}")
        for url in page.copy_urls:
            require(local_file(urljoin(html_url, url)) == markdown.resolve(),
                    f"copy button selects the wrong source for {relative}")
        for href in page.links:
            if href.startswith("#"):
                continue
            url = urljoin(html_url, href)
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc):
                local_file(url)
    require(set(exported) == expected, "llms.txt does not cover exactly the intended Markdown pages")
    search = json.loads((site / "search.json").read_text())
    require(isinstance(search, dict) and isinstance(search.get("items"), list)
            and bool(search["items"]), "empty or invalid search index: expected nonempty items")
    return {"status": "ok", "pages": len(sources), "llm_pages": len(exported), "deployed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", default=".", help="Project root containing zensical.toml")
    args = parser.parse_args()
    try:
        result = check(Path(args.target_dir).resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(json.dumps({"status": "failed", "deployed": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
