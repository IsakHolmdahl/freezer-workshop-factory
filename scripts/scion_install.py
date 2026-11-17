"""Install the Scion CLI shim alongside the unmodified upstream executable.

Uses stdlib file operations and exec dispatch; no additional dependency is needed.
"""
import argparse
import os
from pathlib import Path
import shutil
import shlex


SHIM = Path(__file__).with_name("scion-shim.sh")
MARKER = b"# ASF_SCION_SHIM\n"


def is_shim(path):
    path = Path(path)
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return MARKER in stream.read(128)


def upstream_binary(path):
    path = Path(path).resolve()
    return path.with_name(path.name + "-real") if is_shim(path) else path


def install_binary(source, target):
    """Atomic replacement also works while the old executable is running."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.new")
    shutil.copy2(source, tmp)
    tmp.chmod(0o755)
    os.replace(tmp, target)


def install_scion(source, target):
    target = Path(target).expanduser().absolute()
    source = upstream_binary(source)
    if not source.is_file():
        raise FileNotFoundError(f"upstream Scion binary not found: {source}")
    install_binary(source, target.with_name(target.name + "-real"))
    for extension in ("local", "cross-project", "sessions"):
        install_binary(SHIM.with_name(f"scion-{extension}.sh"),
                       target.with_name(f"{target.name}-{extension}.sh"))
    install_binary(SHIM, target)
    install_completion(target)


def install_completion(target):
    """Keep one managed completion block in the user's shell startup file."""
    shell = Path(os.environ.get("SHELL", "")).name
    quoted = shlex.quote(str(target))
    command = f"{quoted} completion {shell}"
    if shell == "bash":
        activation = f"source <({command})"
        config = Path.home() / ".bashrc"
        lines = f"if [ -x {quoted} ]; then\n    {activation}\nfi"
    elif shell == "zsh":
        activation = f"(( $+functions[compdef] )) || {{ autoload -Uz compinit && compinit; }}\n    source <({command})"
        config = Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"
        lines = f"if [ -x {quoted} ]; then\n    {activation}\nfi"
    elif shell == "fish":
        # Fish single quotes escape apostrophes with a backslash (unlike POSIX sh).
        quoted = "'" + str(target).replace("\\", "\\\\").replace("'", "\\'") + "'"
        command = f"{quoted} completion {shell}"
        activation = f"{command} | source"
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        config = Path(os.environ.get("__fish_config_dir") or config_home / "fish") / "config.fish"
        lines = f"if test -x {quoted}\n    {activation}\nend"
    else:
        print("Shell completion: run scion completion bash|zsh|fish and source the output.")
        return
    start = "# >>> ASF Scion completion >>>"
    end = "# <<< ASF Scion completion <<<"
    block = f"{start}\n{lines}\n{end}"
    original = config.read_text() if config.exists() else ""
    if start in original:
        before, _, remaining = original.partition(start)
        _, found, after = remaining.partition(end)
        if not found:
            raise ValueError(f"Incomplete ASF Scion completion block in {config}")
        updated = before + block + after
    else:
        separator = "\n" if original and not original.endswith("\n") else ""
        updated = original + separator + block + "\n"
    if updated != original:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(updated)
    print(f"Scion completion configured in {config}; open a new shell to activate it.")


def main():
    import factory_env as fe

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", type=Path, help="upstream binary (default: existing installation, then scion/build/scion)")
    parser.add_argument("--install-path", type=Path,
                        default=Path(os.environ.get("INSTALL_PATH") or fe.install_path()))
    args = parser.parse_args()
    source = args.binary
    if source is None:
        installed = upstream_binary(args.install_path.expanduser())
        source = installed if installed.is_file() else fe.SCION_SRC / "build/scion"
    if not upstream_binary(source).is_file():
        parser.error("no existing Scion binary found; run make setup-scion first, or pass --binary PATH")
    install_scion(source, args.install_path)
    print(f"Installed Scion shim at {args.install_path}; use scion local --help")


if __name__ == "__main__":
    main()
