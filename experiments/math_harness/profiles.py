"""Resolve registered profiles and the source trees frozen with each bundle."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPERIMENTS = ("math_pilot", "math_pilot_v4", "math_pilot_matroid_v1", "math_pilot_matching_v1")
DEFAULT_EXPERIMENT = "math_pilot_v4"


def profile_dir(config):
    name = config if isinstance(config, str) else config["experiment"]
    if name not in EXPERIMENTS:
        raise ValueError(f"Unknown experiment profile: {name!r}")
    return HERE.parent / name


def source_roots(config):
    return (HERE, profile_dir(config), ROOT / "runtime", ROOT / "board")


def source_files(config):
    for folder in source_roots(config):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".sh", ".txt", ".json", ".md", ".patch"}:
                yield path
