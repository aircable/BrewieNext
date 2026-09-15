import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from program_sync import ProgramSync, ProgramSyncError


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


class ProgramSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        git("init", "--bare", str(self.remote))
        git("init", "-b", "main", str(self.seed))
        git("config", "user.name", "Test", cwd=self.seed)
        git("config", "user.email", "test@example.invalid", cwd=self.seed)
        (self.seed / "workflows").mkdir()
        (self.seed / "schemas").mkdir()
        (self.seed / "catalog").mkdir()
        (self.seed / "procedures/brewing").mkdir(parents=True)
        (self.seed / "workflows/beer_brewing.yml").write_text("name: beer_brewing\n")
        (self.seed / "catalog/programs.yml").write_text("programs: []\n")
        (self.seed / "schemas/procedure.schema.json").write_text("{}\n")
        (self.seed / "schemas/workflow.schema.json").write_text("{}\n")
        (self.seed / "procedures/brewing/prepare.yml").write_text("name: prepare\n")
        git("add", "-A", cwd=self.seed)
        git("commit", "-m", "Initial", cwd=self.seed)
        git("remote", "add", "origin", str(self.remote), cwd=self.seed)
        git("push", "-u", "origin", "main", cwd=self.seed)
        self.config = self.root / "github.json"
        self.config.write_text(json.dumps({
            "repository": str(self.remote), "branch": "main",
        }))
        self.worktree = self.root / "programs/source"
        self.sync = ProgramSync(str(self.config), str(self.worktree))

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def validate(path):
        if not (path / "workflows/beer_brewing.yml").is_file():
            raise ProgramSyncError("missing workflow")

    def test_load_save_and_no_change(self):
        loaded = self.sync.load(self.validate)
        self.assertTrue(loaded["initialized"])
        self.assertFalse(loaded["dirty"])

        procedure = self.worktree / "procedures/brewing/prepare.yml"
        procedure.write_text("name: prepare\ndescription: changed\n")
        saved = self.sync.save(self.validate)
        self.assertTrue(saved["changed"])
        self.assertFalse(saved["dirty"])

        clone = self.root / "check"
        git("clone", "-b", "main", str(self.remote), str(clone))
        self.assertIn("description: changed", (clone / "procedures/brewing/prepare.yml").read_text())
        self.assertFalse(self.sync.save(self.validate)["changed"])

    def test_load_refuses_unsaved_changes(self):
        self.sync.load(self.validate)
        (self.worktree / "workflows/beer_brewing.yml").write_text("name: local_change\n")
        with self.assertRaisesRegex(ProgramSyncError, "not saved"):
            self.sync.load(self.validate)

    def test_save_refuses_remote_divergence(self):
        self.sync.load(self.validate)
        other = self.root / "other"
        git("clone", "-b", "main", str(self.remote), str(other))
        git("config", "user.name", "Other", cwd=other)
        git("config", "user.email", "other@example.invalid", cwd=other)
        (other / "README.md").write_text("remote change\n")
        git("add", "README.md", cwd=other)
        git("commit", "-m", "Remote", cwd=other)
        git("push", cwd=other)
        (self.worktree / "workflows/beer_brewing.yml").write_text("name: local_change\n")
        with self.assertRaisesRegex(ProgramSyncError, "GitHub changed"):
            self.sync.save(self.validate)


if __name__ == "__main__":
    unittest.main()
