"""Terminal helpers shared by the setup steps: headings, questions and do-nothing guides.

Prompts answer with their default when ASSUME_YES is set (`--yes`), so a step can
run unattended. Output goes through rich; nothing here runs a process.
"""
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
import time
from contextlib import contextmanager

console = Console(highlight=False)
ASSUME_YES = False
_step_number = [0]


def set_assume_yes(value: bool) -> None:
    global ASSUME_YES
    ASSUME_YES = value


def heading(title: str) -> None:
    _step_number[0] += 1
    console.rule(f"[bold cyan]{_step_number[0]}. {escape(title)}", align="left", style="cyan")


def info(message: str) -> None:
    console.print(f"    {message}", markup=False)


@contextmanager
def activity(description):
    info(description)
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), TimeElapsedColumn(),
                  console=console, transient=True) as progress:
        progress.add_task(description, total=None)
        yield


def wait_for(description, probe, timeout, interval=3):
    """Poll an automated check while showing activity and elapsed time."""
    deadline = time.monotonic() + timeout
    info(f"{description} (up to {timeout}s; Ctrl-C to interrupt)")
    with activity(description):
        while True:
            if probe():
                info(f"Ready: {description}")
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                info(f"Timed out: {description}")
                return False
            time.sleep(min(interval, remaining))


def ask(question: str, default: str = "") -> str:
    if ASSUME_YES and default:
        info(f"{question} -> {default}")
        return default
    suffix = f" [{default}]" if default else ""
    answer = input(f"    {question}{suffix}: ").strip()
    return answer or default


def confirm(question: str, default: bool = True) -> bool:
    answer = ask(f"{question} (y/n)", "y" if default else "n").lower()
    return answer.startswith("y")


def choose(question: str, options: dict[str, str]) -> str:
    """options: {key: description}; returns the chosen key."""
    keys = list(options)
    for number, key in enumerate(keys, 1):
        info(f"  {number}. {key:<8} {options[key]}")
    while True:
        answer = ask(question, keys[0])
        if answer in options:
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        info(f"Please answer 1-{len(keys)} or one of: {', '.join(keys)}")


class Quit(Exception):
    """The user typed 'q' at a paste prompt; the caller should stop."""


def paste(prompt: str) -> str | None:
    """Read pasted, possibly multi-line output; an empty line ends it.

    Returns None when the user skips ('s'). Raises Quit on 'q'.
    """
    print(f"    {prompt} (finish with an empty line; 's' = skip, 'q' = quit):")
    lines: list[str] = []
    while True:
        line = input("    > ")
        if not lines and line.strip().lower() in ("s", "q"):
            if line.strip().lower() == "q":
                raise Quit()
            return None
        if not line.strip():
            if lines:
                return "\n".join(lines)
            continue
        lines.append(line)


def guide(what: str, steps: list[str], check_command: str, validate, hint_on_fail: str = "") -> bool:
    """Do-nothing step: say what to do, then which command to run; check the pasted output.

    validate(pasted_text) -> (ok, message). Returns True when done, False when skipped.
    Raises Quit when the user quits.
    """
    info(what)
    for number, step in enumerate(steps, 1):
        info(f"  {number}. {step}")
    while True:
        info(f"Then run this in a terminal:  {check_command}")
        output = paste("Paste the output here")
        if output is None:
            info("Skipped. ./setup.py --doctor will remind you.")
            return False
        ok, message = validate(output)
        info(("OK: " if ok else "Not yet: ") + message)
        if ok:
            return True
        if hint_on_fail:
            info(hint_on_fail)


def checkbox(message: str, choices: list[tuple[str, str, bool]]) -> list[str]:
    """Multi-select with plain [x]/[ ] boxes; choices: [(title, value, checked)]. Returns the picked values."""
    import questionary
    from questionary.prompts import common

    # No colour for ticked lines; the pointer (>) marks the current line.
    common.INDICATOR_SELECTED, common.INDICATOR_UNSELECTED = "[x]", "[ ]"
    style = questionary.Style([("selected", "noreverse"), ("highlighted", "bold"),
                               ("pointer", "bold"), ("text", "")])
    picked = questionary.checkbox(
        message, instruction="(space: tick/untick, enter: confirm)",
        choices=[questionary.Choice(title, value=value, checked=checked) for title, value, checked in choices],
        pointer=">", style=style).ask()
    if picked is None:
        raise KeyboardInterrupt
    return picked
