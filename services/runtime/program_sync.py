"""Safe Git-backed persistence for the Brewie procedure authoring repository."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class ProgramSyncError(RuntimeError):
    """A user-actionable repository synchronization failure."""


class ProgramSync:
    def __init__(self, config_path: str, worktree: str):
        self.config_path = Path(config_path)
        self.worktree = Path(worktree)
        self._lock = threading.Lock()

    def _config(self):
        if not self.config_path.is_file():
            raise ProgramSyncError(f"GitHub configuration is missing: {self.config_path}")
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProgramSyncError(f"GitHub configuration is invalid: {error}") from None
        repository = config.get("repository")
        branch = config.get("branch", "main")
        if not isinstance(repository, str) or not repository.strip():
            raise ProgramSyncError("GitHub configuration requires 'repository'")
        parsed = urlsplit(repository)
        if parsed.username or parsed.password:
            raise ProgramSyncError("Repository URLs must not contain credentials")
        if parsed.scheme and parsed.scheme != "https":
            raise ProgramSyncError("Repository URL must use HTTPS")
        if not parsed.scheme and not Path(repository).is_absolute():
            raise ProgramSyncError("Repository must be an HTTPS URL")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", str(branch)) or ".." in branch:
            raise ProgramSyncError("Configured Git branch is invalid")
        token_file = config.get("token_file")
        if token_file and not Path(token_file).is_file():
            raise ProgramSyncError(f"GitHub token file is missing: {token_file}")
        if token_file and Path(token_file).stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ProgramSyncError("GitHub token file must only be accessible by its owner (chmod 600)")
        return config

    @staticmethod
    def _safe_repository(repository):
        parsed = urlsplit(repository)
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname += f":{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, parsed.fragment))

    def _git_env(self, config, temporary):
        environment = os.environ.copy()
        environment.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        })
        token_file = config.get("token_file")
        if token_file:
            askpass = Path(temporary) / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf '%s\\n' \"$BREWIE_GIT_USERNAME\" ;;\n"
                "  *) cat \"$BREWIE_GIT_TOKEN_FILE\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            environment.update({
                "GIT_ASKPASS": str(askpass),
                "BREWIE_GIT_USERNAME": str(config.get("username", "x-access-token")),
                "BREWIE_GIT_TOKEN_FILE": str(token_file),
            })
        return environment

    def _run(self, args, config, cwd=None, check=True):
        with tempfile.TemporaryDirectory(prefix="brewie-git-auth-") as temporary:
            result = subprocess.run(
                ["git", *args], cwd=cwd, env=self._git_env(config, temporary),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=120, check=False,
            )
        if check and result.returncode:
            detail = (result.stderr or result.stdout or "Git command failed").strip()
            token_file = config.get("token_file")
            if token_file:
                try:
                    token = Path(token_file).read_text(encoding="utf-8").strip()
                    if token:
                        detail = detail.replace(token, "[redacted]")
                except OSError:
                    pass
            repository = config.get("repository", "")
            detail = detail.replace(repository, self._safe_repository(repository))
            raise ProgramSyncError(detail.splitlines()[-1][:500])
        return result

    def _is_checkout(self):
        return (self.worktree / ".git").exists()

    def _status_unlocked(self, config=None):
        try:
            config = config or self._config()
        except ProgramSyncError as error:
            return {
                "configured": False, "initialized": False, "dirty": False,
                "repository": None, "branch": None, "head": None,
                "ahead": 0, "behind": 0, "error": str(error),
            }
        status = {
            "configured": True,
            "initialized": self._is_checkout(),
            "dirty": False,
            "repository": self._safe_repository(config["repository"]),
            "branch": config.get("branch", "main"),
            "head": None,
            "ahead": 0,
            "behind": 0,
            "error": None,
        }
        if not status["initialized"]:
            return status
        try:
            porcelain = self._run(["status", "--porcelain"], config, self.worktree).stdout
            status["dirty"] = bool(porcelain.strip())
            status["head"] = self._run(["rev-parse", "--short=12", "HEAD"], config, self.worktree).stdout.strip()
            counts = self._run(
                ["rev-list", "--left-right", "--count", f"HEAD...origin/{status['branch']}"],
                config, self.worktree, check=False,
            )
            if counts.returncode == 0:
                status["ahead"], status["behind"] = (int(value) for value in counts.stdout.split())
        except (ProgramSyncError, ValueError) as error:
            status["error"] = str(error)
        return status

    def status(self):
        with self._lock:
            return self._status_unlocked()

    def load(self, validate):
        """Fetch a clean remote snapshot, validate it, then atomically activate it."""
        with self._lock:
            config = self._config()
            branch = config.get("branch", "main")
            if self._is_checkout():
                if self._run(["status", "--porcelain"], config, self.worktree).stdout.strip():
                    raise ProgramSyncError("Local procedure changes are not saved; Save before loading")
                self._run(["fetch", "origin", branch], config, self.worktree)
                local_head = self._run(["rev-parse", "HEAD"], config, self.worktree).stdout.strip()
                remote_head = self._run(["rev-parse", f"origin/{branch}"], config, self.worktree).stdout.strip()
                if local_head != remote_head:
                    contained = self._run(
                        ["merge-base", "--is-ancestor", local_head, remote_head],
                        config, self.worktree, check=False,
                    )
                    if contained.returncode != 0:
                        raise ProgramSyncError("Local commits are not on GitHub; Save before loading")

            self.worktree.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="programs-load-", dir=self.worktree.parent))
            backup = self.worktree.with_name(self.worktree.name + ".previous")
            try:
                self._run([
                    "clone", "--single-branch", "--branch", branch,
                    "--", config["repository"], str(staging),
                ], config)
                validate(staging)
                if backup.exists():
                    shutil.rmtree(backup)
                if self.worktree.exists():
                    self.worktree.rename(backup)
                staging.rename(self.worktree)
                if backup.exists():
                    shutil.rmtree(backup)
            except Exception:
                if not self.worktree.exists() and backup.exists():
                    backup.rename(self.worktree)
                raise
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            return self._status_unlocked(config)

    def save(self, validate):
        """Validate, commit all authoring changes, and push directly to configured main."""
        with self._lock:
            config = self._config()
            branch = config.get("branch", "main")
            if not self._is_checkout():
                raise ProgramSyncError("Load from GitHub once before saving")
            validate(self.worktree)
            self._run(["fetch", "origin", branch], config, self.worktree)
            remote = self._run(["rev-parse", f"origin/{branch}"], config, self.worktree).stdout.strip()
            head = self._run(["rev-parse", "HEAD"], config, self.worktree).stdout.strip()
            if head != remote:
                remote_is_ancestor = self._run(
                    ["merge-base", "--is-ancestor", remote, head], config, self.worktree, check=False
                )
                if remote_is_ancestor.returncode != 0:
                    raise ProgramSyncError("GitHub changed since the last load; Load before saving")
            if self._run(["status", "--porcelain"], config, self.worktree).stdout.strip():
                self._run(["add", "-A"], config, self.worktree)
                self._run([
                    "-c", f"user.name={config.get('author_name', 'Brewie Studio')}",
                    "-c", f"user.email={config.get('author_email', 'brewie@localhost')}",
                    "commit", "-m", "Save procedures from Brewie Studio",
                ], config, self.worktree)
            new_head = self._run(["rev-parse", "HEAD"], config, self.worktree).stdout.strip()
            changed = new_head != remote
            if changed:
                self._run(["push", "origin", f"HEAD:refs/heads/{branch}"], config, self.worktree)
            result = self._status_unlocked(config)
            result["changed"] = changed
            return result
