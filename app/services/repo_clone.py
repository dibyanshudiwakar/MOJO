"""Real git clone and push, using a user's stored GitHub connection.

Nothing in this codebase talked to a remote repository before this: every
existing agent and service assumes its input is already checked out locally
under the workspace root (see ``workspace.resolve``). This module is what
finally gives the "Repo Link" fields in the migration wizard something to act
on, using the same GitHub connection built for status-checking.

Credential handling: a personal access token is a live credential, so it must
never appear in argv (visible to any other process on the host via a process
listing) or in the repository's own remote URL (visible forever afterward via
``git remote -v`` or ``.git/config``). Git's ``GIT_ASKPASS`` mechanism is used
instead -- a throwaway script that answers whatever prompt git shows with the
token, read from a process-local environment variable that is never part of
the command line itself.

That alone was not sufficient in practice: a typical Git for Windows install
(and this host) configures ``credential.helper=manager`` (Git Credential
Manager) system-wide. GCM is a *separate* credential provider layered above
plain git, with its own built-in GitHub OAuth device flow -- it does not
reliably defer to ``GIT_ASKPASS``/``GIT_TERMINAL_PROMPT``, since those only
govern git's own fallback prompt, not a helper's independent UI. The
observed symptom was exactly "why is it asking me to sign in to GitHub when
I already gave it a token" during a pipeline's clone step, even though the
stored token was correct and being supplied. Every git invocation this
module makes therefore explicitly disables the configured helper
(``-c credential.helper=``, see ``RepoCloneService._run``) so the token from
``GIT_ASKPASS`` is the only credential path git can use.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from app.services import workspace
from app.services.github_integration import GitHubIntegrationService

TIMEOUT = 180


class RepoCloneError(RuntimeError):
    """A clone or push failed. Carries the real git detail, trimmed."""


def _host(url):
    return (urlparse(url).hostname or '').lower()


class _Askpass:
    """A short-lived helper git can invoke instead of prompting interactively.

    Always answers with the token regardless of which prompt (username or
    password) git is asking, which is the common, working simplification for
    a host that accepts a PAT as either -- GitHub and GitHub Enterprise Server
    both do.
    """

    def __init__(self, token):
        self._dir = tempfile.mkdtemp(prefix='ace-askpass-')
        if os.name == 'nt':
            self.path = str(Path(self._dir) / 'askpass.cmd')
            Path(self.path).write_text('@echo %ACE_GIT_TOKEN%\r\n', encoding='utf-8')
        else:
            self.path = str(Path(self._dir) / 'askpass.sh')
            Path(self.path).write_text('#!/bin/sh\necho "$ACE_GIT_TOKEN"\n', encoding='utf-8')
            os.chmod(self.path, stat.S_IRWXU)
        self.env = dict(os.environ)
        self.env.update({
            'GIT_ASKPASS': self.path, 'GIT_TERMINAL_PROMPT': '0', 'ACE_GIT_TOKEN': token,
            # Git for Windows commonly ships with Git Credential Manager
            # configured as credential.helper=manager (this host has exactly
            # that). GCM is its own credential provider with built-in GitHub
            # OAuth support -- it can pop up a real "sign in to GitHub"
            # window, and it does not reliably respect GIT_ASKPASS /
            # GIT_TERMINAL_PROMPT, since those only govern git's *own*
            # fallback prompt, not a helper's independent UI. GCM_INTERACTIVE
            # is belt-and-suspenders; disabling the helper entirely (below,
            # via -c credential.helper=) is what actually stops it from being
            # invoked at all for this process.
            'GCM_INTERACTIVE': 'never',
        })

    def __enter__(self):
        return self.env

    def __exit__(self, *exc):
        shutil.rmtree(self._dir, ignore_errors=True)
        return False


class RepoCloneService:
    def __init__(self, repo):
        self.repo = repo
        self.github = GitHubIntegrationService(repo)

    def _credential(self, user_id, repo_url):
        """The token for this repo's host, or a clear error if none is stored.

        A mismatched host is refused outright rather than attempted anyway --
        sending a github.com token to an unrelated host is not something a
        caller-supplied URL should ever be able to trigger silently.
        """
        state = self.github.config(user_id)
        if not state.get('configured'):
            raise RepoCloneError('github_not_configured')
        if _host(repo_url) != _host(state['url']):
            raise RepoCloneError('repo_host_does_not_match_connected_github:%s' % _host(repo_url))
        return self.github.token_for(user_id)

    def _run(self, args, cwd, env):
        """Run ``git <args>``, always with the configured credential helper disabled.

        ``credential.helper`` (Git Credential Manager, on this host and most
        Windows git installs) is a separate credential provider from
        ``GIT_ASKPASS`` -- if it stays enabled it can intercept a clone/push
        before the askpass script is ever consulted and show its own
        interactive GitHub sign-in, which is exactly the "why is it asking me
        to log in when I already gave it a token" symptom this guards
        against. Applied to every invocation this module makes, not only the
        ones that touch the network, so there is no call site left to forget.
        """
        cmd = ['git', '-c', 'credential.helper='] + args
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise RepoCloneError('git_command_timed_out:%s' % ' '.join(args[:1])) from exc
        if r.returncode != 0:
            raise RepoCloneError('git_command_failed:%s' % (r.stderr or r.stdout or 'no detail').strip()[:400])
        return r.stdout

    def clone(self, user_id, repo_url, branch, dest):
        """Clone ``repo_url`` at ``branch`` into a confined destination directory."""
        url = workspace.runtime_url(repo_url)
        ref = workspace.git_ref(branch)
        target = workspace.resolve(dest, must_exist=False, create=False)
        if target.exists() and any(target.iterdir()):
            raise RepoCloneError('clone_destination_not_empty')
        target.parent.mkdir(parents=True, exist_ok=True)
        token = self._credential(user_id, url)
        with _Askpass(token) as env:
            self._run(['clone', '--branch', ref, '--single-branch', '--depth', '1', url, str(target)], cwd=str(target.parent), env=env)
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(target), capture_output=True, text=True, timeout=30)
        return {'path': str(target), 'branch': ref, 'commit': commit.stdout.strip() if commit.returncode == 0 else ''}

    def push(self, user_id, repo_url, branch, source_dir, commit_message='Generated Spring Boot code'):
        """Push the contents of ``source_dir`` to ``branch`` on ``repo_url``.

        Clones fresh into a scratch directory, replaces its tracked content
        with what was generated, and commits and pushes -- the generated
        output is the source of truth for this push, not a working tree the
        caller hands us with its own uncommitted state.
        """
        url = workspace.runtime_url(repo_url)
        ref = workspace.git_ref(branch)
        source = workspace.resolve(source_dir, must_exist=True)
        token = self._credential(user_id, url)
        scratch = Path(tempfile.mkdtemp(prefix='ace-push-'))
        try:
            with _Askpass(token) as env:
                try:
                    self._run(['clone', '--branch', ref, '--single-branch', '--depth', '1', url, str(scratch)], cwd=str(scratch.parent), env=env)
                except RepoCloneError:
                    # The branch may not exist yet -- clone the default branch
                    # and create it locally, which `git push` below then
                    # publishes as a new remote branch.
                    self._run(['clone', '--depth', '1', url, str(scratch)], cwd=str(scratch.parent), env=env)
                    self._run(['checkout', '-b', ref], cwd=str(scratch), env=env)
                for item in scratch.iterdir():
                    if item.name == '.git':
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                for item in source.iterdir():
                    dest = scratch / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
                self._run(['add', '-A'], cwd=str(scratch), env=env)
                self._run(['-c', 'user.email=ace-migration-factory@local', '-c', 'user.name=ACE Modernization Accelerator', 'commit', '-m', commit_message, '--allow-empty'], cwd=str(scratch), env=env)
                self._run(['push', '-u', 'origin', ref], cwd=str(scratch), env=env)
                commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(scratch), capture_output=True, text=True, timeout=30)
            return {'branch': ref, 'commit': commit.stdout.strip() if commit.returncode == 0 else ''}
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
