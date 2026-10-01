"""Build source-only desktop and skills-only directory ZIPs from explicit allowlists."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EDITIONS = {
    "desktop": ("ai-meeting-notes", {"plugin.json", "mcp.json", ".mcp.json", ".app.json", "config.json"},
                {".codex-plugin", "assets", "skills", "server", "native", "scripts"}),
    "directory": ("meeting-notes-transcripts", {"plugin.json"}, {".codex-plugin", "assets", "skills"}),
}


def package(edition, output_dir=None):
    name, files, folders = EDITIONS[edition]
    plugin = ROOT / "plugins" / name
    output = Path(output_dir or ROOT / "dist") / (name + ".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(plugin.rglob("*")):
            relative = path.relative_to(plugin)
            if (path.is_file() and not path.is_symlink()
                    and (str(relative) in files or relative.parts[0] in folders)
                    and not any(p in {"__pycache__", ".build", ".DS_Store"} for p in relative.parts)
                    and path.suffix not in {".pyc", ".pyo"}):
                archive.write(path, relative)
        archive.write(ROOT / "LICENSE", "LICENSE")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=[*EDITIONS, "all"], default="all")
    args = parser.parse_args()
    for edition in EDITIONS if args.edition == "all" else [args.edition]:
        print(package(edition))
