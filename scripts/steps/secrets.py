"""Store an optional GitHub token directly in the Hub so agents can clone and push.

Create a dedicated PAT with selected repository access, enter it in a hidden
prompt, and validate GitHub API access before publishing. Skipping is allowed;
use --replace to rotate a Hub token and --keyring for optional desktop storage.
Credentials are not saved in .env.secrets.

    python scripts/steps/secrets.py --dry-run
"""
import getpass
import json
import re
import sys
from urllib.parse import urlsplit

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.secrets: scripts/ is already on sys.path
    pass

import requests
from dotenv import dotenv_values, unset_key

import factory_env as fe
import credential_store
from steps import ui
from steps.common import Context, StepError, StepResult, cli

TOKEN_RE = re.compile(r"^(gh[pousr]_|github_pat_)[A-Za-z0-9_]+$")
GITHUB_USER_URL = "https://api.github.com/user"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SCION_FLAGS = ["--global", "--yes", "--non-interactive"]


def parse_repositories(value):
    """Accept OWNER/REPO and GitHub clone URLs without accepting other hosts."""
    repositories = []
    for item in value.replace(",", " ").split():
        if item.startswith("git@github.com:"):
            item = item[len("git@github.com:"):]
        elif "://" in item:
            url = urlsplit(item)
            if url.scheme not in ("http", "https", "ssh") or url.hostname != "github.com" or url.query or url.fragment:
                raise ValueError("Use OWNER/REPO or a GitHub repository URL")
            if url.password or (url.username and url.username != "git"):
                raise ValueError("Repository URLs must not contain credentials")
            item = url.path.strip("/")
        else:
            item = item.removeprefix("github.com/").rstrip("/")
        item = item.removesuffix(".git")
        if not REPO_RE.fullmatch(item) or any(part in (".", "..") for part in item.split("/")):
            raise ValueError("Use OWNER/REPO or a GitHub repository URL")
        if item not in repositories:
            repositories.append(item)
    if not repositories:
        raise ValueError("Enter at least one repository, or s to skip")
    return repositories


def check_token(candidate: str, repositories=()) -> tuple[bool, str]:
    """(ok, message). Network failures are reported, not raised."""
    if not TOKEN_RE.match(candidate):
        return False, "that does not look like a GitHub token (starts with gho_, ghp_ or github_pat_)"
    try:
        headers = {"Authorization": f"Bearer {candidate}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        reply = requests.get(GITHUB_USER_URL, timeout=10, headers=headers)
        if not reply.ok:
            return False, f"GitHub rejected the token (HTTP {reply.status_code}); check expiry and organization approval/SSO"
        login = reply.json().get("login")
        scopes = {scope.strip() for scope in reply.headers.get("X-OAuth-Scopes", "").split(",")}
        if candidate.startswith("ghp_") and "repo" not in scopes:
            return False, "classic PAT needs the repo scope; prefer a fine-grained PAT limited to selected repositories"
        for repository in repositories:
            if not REPO_RE.fullmatch(repository):
                return False, f"invalid repository {repository!r}; use OWNER/REPO"
            base = f"https://api.github.com/repos/{repository}"
            for suffix, capability in (("", "repository metadata"), ("/contents", "Contents"),
                                       ("/pulls?per_page=1", "Pull requests")):
                probe = requests.get(base + suffix, timeout=10, headers=headers)
                if not probe.ok:
                    # Empty repositories have no contents yet.
                    if suffix == "/contents" and probe.status_code == 404:
                        empty = requests.get(base + "/git/matching-refs/heads/", timeout=10, headers=headers)
                        if empty.ok and empty.json() == []:
                            continue
                    return False, (f"{repository}: cannot access {capability} (HTTP {probe.status_code}); "
                                   "check selected repositories, permissions and organization approval/SSO")
                if not suffix and probe.json().get("permissions", {}).get("push") is False:
                    return False, f"{repository}: the token owner does not have push access"
    except requests.RequestException:
        return False, "could not reach GitHub; check network/proxy and retry"
    except ValueError:
        return False, "GitHub returned an invalid response; retry"
    return True, (f"authenticated as {login}; checked {len(repositories)} repository/repositories. "
                  "Read-only checks cannot prove fine-grained write permissions; confirm Contents and Pull requests: Read and write.")


def _hub_has_token(adapter) -> bool:
    result = adapter.scion([*SCION_FLAGS, "hub", "env", "list", "--scope", "hub", "--json"])
    if not result.ok:
        raise StepError("Could not check the Scion Hub for GITHUB_TOKEN",
                        ["check Scion Hub login/admin access with make doctor"])
    try:
        return any(item["key"] == "GITHUB_TOKEN" for item in json.loads(result.stdout)["envVars"])
    except (ValueError, KeyError, TypeError):
        raise StepError("Invalid environment response from the Scion Hub", ["make doctor"]) from None


def _clear_legacy_token() -> None:
    if fe.SECRETS_FILE.exists() and "GITHUB_TOKEN" in dotenv_values(fe.SECRETS_FILE):
        unset_key(fe.SECRETS_FILE, "GITHUB_TOKEN")


def _publish(adapter, token) -> None:
    # Do not include CLI output in errors: it may contain the supplied credential.
    result = adapter.scion([*SCION_FLAGS, "hub", "env", "set", "--scope", "hub", "--always",
                            "--secret", "GITHUB_TOKEN", token])
    if not result.ok:
        raise StepError("Could not publish GITHUB_TOKEN to the Scion Hub",
                        ["check Scion Hub login/admin access with make doctor", "make setup-github"])
    _clear_legacy_token()


def run(ctx: Context, **options) -> StepResult:
    if ctx.dry_run:
        ctx.log("would validate a GitHub token and publish it directly to the Hub")
        return StepResult("dry-run", "GitHub token: would prompt")
    if not options.get("replace"):
        if _hub_has_token(ctx.adapter):
            _clear_legacy_token()
            return StepResult("skipped", "GITHUB_TOKEN already configured in the Hub")
        # Migrate tokens saved by older setup versions only after successful validation/publication.
        legacy = dotenv_values(fe.SECRETS_FILE).get("GITHUB_TOKEN") if fe.SECRETS_FILE.exists() else None
        if not fe.is_placeholder(legacy):
            ok, message = check_token(legacy, fe.get("github.repositories", []) or [])
            if not ok:
                raise StepError(f"GITHUB_TOKEN validation failed: {message}", ["make setup-github-rotate"])
            _publish(ctx.adapter, legacy)
            return StepResult("done", "moved GITHUB_TOKEN from .env.secrets to the Hub")
    preference = fe.get("github.credential_store", "")
    use_keyring = options.get("keyring") or preference == "keyring"
    store = credential_store.backend() if use_keyring or (not ctx.assume_yes and preference != "hub") else None
    if use_keyring and store is None:
        raise StepError("No supported secure desktop keyring is available",
                        ["enable macOS Keychain or Linux Secret Service (KeePassXC is supported)"])
    hub_url = fe.hub_url().rstrip("/")
    if use_keyring and not options.get("replace"):
        stored = credential_store.load(store, hub_url)
        if stored:
            ok, message = check_token(stored, fe.get("github.repositories", []) or [])
            if not ok:
                raise StepError(f"Keyring GITHUB_TOKEN validation failed: {message}", ["make setup-github-rotate"])
            _publish(ctx.adapter, stored)
            fe.set_local("github.credential_store", "keyring")
            return StepResult("done", "published GITHUB_TOKEN from the desktop keyring to the Hub")
    if ctx.assume_yes:
        return StepResult("skipped", "GitHub token skipped under --yes; run this step without --yes")
    ui.info("GitHub repository access is optional and can be configured later.")
    if not ui.confirm("Set up a GitHub PAT for Scion agents now?", default=True):
        ui.info("Configure it later: make setup-github")
        return StepResult("skipped", "GitHub PAT deferred; continuing setup")
    ui.info("Create a dedicated fine-grained PAT for Scion agents:")
    ui.info("  1. Open https://github.com/settings/personal-access-tokens/new")
    ui.info("  2. Choose the resource owner (your account or organization), a descriptive name and an expiry.")
    ui.info("  3. Repository access: Only select repositories. Select only repositories agents should work on.")
    ui.info("  4. Repository permissions: Contents: Read and write; Pull requests: Read and write.")
    ui.info("     Metadata: Read-only is included. Add Issues: Read and write only if agents manage GitHub issues.")
    ui.info("     Add Workflows: Read and write only if agents must edit .github/workflows files.")
    ui.info("  5. Generate the token; obtain organization approval if required before continuing.")
    ui.info("Classic PAT fallback: repo scope (and workflow only for workflow edits); fine-grained limits repository access.")
    ui.info("Setup publishes it as a secret Hub-scoped GITHUB_TOKEN environment variable")
    ui.info("with always injection for agents on this Hub. New/restarted agents receive it.")
    default = " ".join(fe.get("github.repositories", []) or [])
    while True:
        entered = ui.ask("Repositories to validate (OWNER/REPO or GitHub URLs, separated by spaces; s to skip)", default or "s")
        if entered.strip().lower() in ("s", "q", "skip"):
            return StepResult("skipped", "GitHub token: skipped")
        try:
            repositories = parse_repositories(entered)
            break
        except ValueError as error:
            ui.info(str(error))
            default = ""
    while True:
        try:
            candidate = getpass.getpass("    Paste the PAT (hidden; s = skip): ").strip()
        except EOFError:
            raise StepError("No input available for the GitHub PAT prompt",
                            ["run make setup-github in an interactive terminal; enter s to skip explicitly"]) from None
        if candidate.lower() in ("s", "q", "skip"):
            return StepResult("skipped", "GitHub token: skipped")
        if not candidate:
            ui.info("No PAT received. Paste the token and press Enter (input is hidden), or enter s to skip.")
            continue
        ok, message = check_token(candidate, repositories)
        ui.info(message)
        if ok:
            break
    if store is not None and (use_keyring or ui.confirm("Also save this PAT in your desktop keyring for reuse?", default=True)):
        credential_store.save(store, hub_url, candidate)
        fe.set_local("github.credential_store", "keyring")
    _publish(ctx.adapter, candidate)
    fe.set_local("github.repositories", repositories)
    return StepResult("done", "validated and published GITHUB_TOKEN directly to the Hub")


def add_arguments(parser):
    parser.add_argument("--replace", action="store_true", help="replace the Hub PAT")
    parser.add_argument("--keyring", action="store_true", help="store/reuse the PAT in the desktop keyring")


main = cli(run, add_arguments, __doc__)

if __name__ == "__main__":
    sys.exit(main())
