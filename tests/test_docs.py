import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs" / "guide").glob("*.md"))]


def test_guides_exist():
    for name in ("write-a-pack.md", "config.md"):
        assert (ROOT / "docs" / "guide" / name).exists(), name


def test_readme_links_resolve():
    for doc in DOCS:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (doc.parent / target).exists(), f"{doc.name}: broken link {target}"


def test_guide_code_imports():
    text = (ROOT / "docs" / "guide" / "write-a-pack.md").read_text()
    lines = re.findall(r"^from pcagent[\w.]* import [^\n]+$", text, flags=re.M)
    assert lines
    for line in lines:
        exec(line, {})
