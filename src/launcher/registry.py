"""Studio registry: loads studios.yaml (plus an optional local override)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "studios.yaml"


@dataclass
class Studio:
    id: str
    name: str
    repo: str
    port: int
    launch: str
    dirs: list[str]
    install_root: str
    short: str = ""
    category: str = ""
    branch: str = "main"
    marker: str = "run.sh"
    installed_marker: str = ""
    scheme: str = "https"
    health: str = "/"
    setup: str = "./setup.sh"
    stop: str = ""
    update: str = ""
    alongside_env: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    processes: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    tagline: str = ""
    pitch: str = ""
    metric: dict[str, str] = field(default_factory=dict)
    bullets: list[str] = field(default_factory=list)
    media: dict[str, str] = field(default_factory=dict)
    gallery: list[dict[str, Any]] = field(default_factory=list)
    icon: str = ""
    icon_small: str = ""        # simpler artwork for 16-30 px (header, tables); falls back to icon
    docs: str = ""
    mla: bool = True

    # -- locations -----------------------------------------------------------
    def candidate_dirs(self) -> list[Path]:
        out = []
        for d in self.dirs:
            p = Path(os.path.expanduser(d))
            out.append(p if p.is_absolute() else Path(self.install_root) / p)
        return out

    def find_dir(self) -> Path | None:
        """The first candidate directory that holds this studio, if any."""
        for p in self.candidate_dirs():
            if (p / self.marker).exists():
                return p
        return None

    def install_dir(self) -> Path:
        """Where the studio is (or would be installed)."""
        return self.find_dir() or self.candidate_dirs()[0]

    def is_installed(self) -> bool:
        d = self.find_dir()
        if d is None:
            return False
        return not self.installed_marker or (d / self.installed_marker).exists()

    def fmt(self, text: str) -> str:
        return text.format(dir=str(self.install_dir()), repo=self.repo,
                           branch=self.branch, port=self.port)

    def process_patterns(self) -> list[str]:
        return [self.fmt(p) for p in self.processes]

    def log_paths(self) -> list[Path]:
        d = self.install_dir()
        return [d / p for p in self.logs]

    def public(self) -> dict[str, Any]:
        """Fields the browser needs."""
        return {
            "id": self.id, "name": self.name, "short": self.short or self.name,
            "category": self.category, "repo": self.repo, "port": self.port,
            "scheme": self.scheme, "tagline": self.tagline, "pitch": self.pitch,
            "metric": self.metric, "bullets": self.bullets, "media": self.media,
            "gallery": self.gallery,
            "icon": self.icon, "icon_small": self.icon_small or self.icon, "docs": self.docs, "dir": str(self.install_dir()),
            "mla": self.mla,
        }


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike | None = None,
         override: str | os.PathLike | None = None) -> list[Studio]:
    """Load the registry. `override` may change install_root, change studios
    by id, or add new ones."""
    data = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text()) or {}
    override = override or os.environ.get("NEAT_LAUNCHER_CONFIG")
    if override and Path(override).is_file():
        over = yaml.safe_load(Path(override).read_text()) or {}
        if "install_root" in over:
            data["install_root"] = over["install_root"]
        by_id = {s["id"]: s for s in data.get("studios", [])}
        for s in over.get("studios", []) or []:
            by_id[s["id"]] = _merge(by_id.get(s["id"], {}), s)
        if over.get("only"):
            by_id = {k: v for k, v in by_id.items() if k in over["only"]}
        data["studios"] = list(by_id.values())
    root = os.environ.get("NEAT_STUDIOS_ROOT") or data.get("install_root", "/media/nvme")
    known = set(Studio.__dataclass_fields__)
    studios = []
    for s in data.get("studios", []):
        fields = {k: v for k, v in s.items() if k in known}
        fields.setdefault("dirs", [s["id"]])
        studios.append(Studio(install_root=root, **fields))
    return studios
