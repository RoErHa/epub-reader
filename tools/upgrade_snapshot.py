"""The rollback point of an upgrade: what --apply moved aside, put back exactly.

Directories (.venv, node_modules) are MOVED, not copied — instant on one
volume, and a moved venv keeps working once it is moved back, because its
scripts name the original path. Files are copied. A directory that did not
exist before is REMOVED on restore, so a failed first install leaves
nothing half-made behind.

The snapshot lives outside the project, so neither the linter nor the test
discovery ever walks a 300 MB venv. It stays until the next --apply, which
is what makes `--rollback` possible after a run that passed every gate but
misbehaves live. STANDARD LIBRARY ONLY (see upgrade_checks.py).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path


class Snapshot:
    def __init__(
        self, project: Path, where: Path, files: list[str], dirs: list[str]
    ) -> None:
        self.project, self.where = Path(project), Path(where)
        self.files, self.dirs = list(files), list(dirs)
        self.manifest = self.where / "manifest.json"

    def save(self) -> None:
        if self.where.exists():
            shutil.rmtree(self.where)
        (self.where / "files").mkdir(parents=True)
        (self.where / "dirs").mkdir()
        present = []
        for name in self.files:
            if (self.project / name).exists():
                shutil.copy2(
                    self.project / name, self.where / "files" / name.replace("/", "__")
                )
        for name in self.dirs:
            if (self.project / name).exists():
                (self.project / name).rename(self.where / "dirs" / name)
                present.append(name)
        self.manifest.write_text(
            json.dumps(
                {
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "files": self.files,
                    "dirs": self.dirs,
                    "present": present,
                }
            )
        )

    def saved_at(self) -> str | None:
        if not self.manifest.exists():
            return None
        return json.loads(self.manifest.read_text()).get("saved_at")

    def restore(self) -> None:
        if not self.manifest.exists():
            raise FileNotFoundError(
                f"nothing to roll back to: {self.manifest} is missing"
            )
        present = set(json.loads(self.manifest.read_text())["present"])
        for name in self.dirs:
            target = self.project / name
            if target.exists():
                shutil.rmtree(target)
            if name in present:
                (self.where / "dirs" / name).rename(target)
        for name in self.files:
            saved = self.where / "files" / name.replace("/", "__")
            if saved.exists():
                shutil.copy2(saved, self.project / name)
