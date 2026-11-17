"""Find .git/config keys that make Scion refuse to create agent worktrees.

Scion (scion/pkg/util/git_config_policy.go, allowVar) refuses to run git in a
linked checkout whose repository config has a key outside its allow-list, and
the agent then fails with "runtime_error ... not on the linked-checkout
allow-list". Tools write such keys as metadata, e.g. `gh` (remote.*.gh-resolved)
and VS Code (branch.*.vscode-merge-base).

This is a key-level port of that allow-list (value checks are left to Scion);
update it when the scion submodule changes the policy.
"""
import subprocess
from pathlib import Path

# (section, needs_subsection) -> allowed names; "*" allows every name.
_TOP, _SUB = False, True
ALLOWED = {
    ("core", _TOP): {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase",
                     "precomposeunicode", "symlinks", "worktree", "autocrlf", "eol", "safecrlf", "whitespace",
                     "commentchar", "quotepath", "longpaths", "protecthfs", "protectntfs", "checkstat",
                     "trustctime", "untrackedcache", "fsyncmethod", "splitindex", "preloadindex", "abbrev",
                     "sparsecheckout", "sparsecheckoutcone", "hookspath", "fsmonitor", "pager"},
    ("extensions", _TOP): {"worktreeconfig", "relativeworktrees", "objectformat", "refstorage"},
    ("remote", _SUB): {"url", "fetch", "pushurl", "tagopt", "prune", "prunetags", "mirror",
                       "fetchrecursesubmodules", "followremotehead"},
    ("branch", _SUB): {"remote", "merge", "rebase", "description", "pushremote", "mergeoptions",
                       "autosetuprebase"},
    ("branch", _TOP): {"autosetupmerge", "autosetuprebase"},
    ("gc", _TOP): {"auto"},
    ("user", _TOP): {"name", "email", "signingkey"},
    ("lfs", _TOP): {"repositoryformatversion", "concurrenttransfers", "dialtimeout", "tlstimeout",
                    "activitytimeout", "fetchrecentrefsdays", "fetchrecentcommitsdays", "fetchrecentremoterefs",
                    "fetchrecentalways", "pruneoffsetdays", "pruneverifyremotealways", "locksverifyeverywhere",
                    "locksverify", "skipdownloaderrors", "setlockablereadonly", "fetchinclude", "fetchexclude",
                    "batch", "transfermaxretries", "contenttype"},
    ("lfs", _SUB): {"access", "locksverify"},
    ("pull", _TOP): {"rebase", "ff", "octopus", "twohead"},
    ("push", _TOP): {"default", "autosetupremote", "followtags", "gpgsign"},
    ("fetch", _TOP): {"prune", "prunetags", "fsckobjects", "output", "parallel"},
    ("commit", _TOP): {"gpgsign", "verbose"},
    ("tag", _TOP): {"gpgsign", "sort", "forcesignannotated"},
    ("init", _TOP): {"defaultbranch"},
    ("maintenance", _TOP): {"auto", "strategy", "autodetach"},
    ("maintenance", _SUB): {"enabled", "schedule", "auto"},
    ("gpg", _TOP): {"format"},
    ("color", _TOP): {"ui", "pager", "advice", "showbranch"},
    ("advice", _TOP): {"*"},
    ("rerere", _TOP): {"enabled", "autoupdate"},
    ("status", _TOP): {"short", "branch", "showstash", "showuntrackedfiles", "submodulesummary", "relativepaths",
                       "displaycommentprefix", "renamelimit", "renames", "aheadbehind"},
    ("diff", _TOP): {"algorithm", "renames", "renamelimit", "indentheuristic", "colormoved", "colormovedws",
                     "context", "interhunkcontext", "mnemonicprefix", "noprefix", "wordregex", "submodule",
                     "relative", "ignoresubmodules", "dirstat", "statgraphwidth", "wserrorhighlight",
                     "suppressblankempty", "colorwords", "tabsize"},
    ("diff", _SUB): {"binary", "wordregex", "xfuncname"},
    ("merge", _TOP): {"conflictstyle", "ff", "renamelimit", "renames", "stat", "log", "autostash", "verbosity",
                      "directoryrenames", "renormalize", "branchdesc"},
    ("merge", _SUB): {"name", "recursive"},
    ("submodule", _TOP): {"recurse"},
    ("submodule", _SUB): {"update", "url", "active", "ignore", "shallow", "branch", "fetchrecursesubmodules"},
    ("filter", _SUB): {"required", "clean", "smudge", "process"},  # filter.lfs.* only, value-checked by Scion
}
COLOR_COMMANDS = {"branch", "diff", "decorate", "grep", "interactive", "log", "push", "remote", "status",
                  "transport", "add"}
# Editor/tool metadata that is safe to delete (the tools only lose a remembered choice).
TOOL_METADATA = ("gh-resolved", "vscode-merge-base", "github-pr-owner-number", "github-pr-base-branch")


def split_key(key):
    """'branch.feature/x.merge' -> ('branch', 'feature/x', 'merge'); subsection None at top level."""
    section, _, rest = key.partition(".")
    subsection, _, name = rest.rpartition(".")
    return section.lower(), (subsection or None), name.lower()


def allowed(key):
    section, subsection, name = split_key(key)
    if section == "color" and subsection is not None:
        return subsection.lower() in COLOR_COMMANDS
    if section == "filter" and subsection != "lfs":
        return False
    names = ALLOWED.get((section, subsection is not None), set())
    return "*" in names or name in names


def refused_keys(repo):
    """Repository-config keys Scion would refuse for a linked checkout at `repo`."""
    result = subprocess.run(["git", "-C", str(repo), "config", "--local", "--list", "--name-only"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return sorted({key for key in result.stdout.split() if not allowed(key)})


def is_tool_metadata(key):
    return split_key(key)[2] in TOOL_METADATA


def remove(repo, key):
    return subprocess.run(["git", "-C", str(repo), "config", "--local", "--unset-all", key],
                          capture_output=True, text=True).returncode == 0


def linked_checkouts(run):
    """Workspace paths of the projects linked on this machine (`scion project list --local`)."""
    import json
    out = run(["scion", "project", "list", "--local", "--format", "json"]).stdout
    try:
        entries = json.loads(out or "[]")
    except ValueError:
        return []
    return [Path(e["workspace_path"]) for e in entries
            if e.get("workspace_path") and Path(e["workspace_path"], ".git").exists()]
