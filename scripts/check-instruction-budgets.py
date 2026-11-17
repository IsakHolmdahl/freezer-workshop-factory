#!/usr/bin/env python3
"""Check template files and essential lifecycle/delegation rules."""

from pathlib import Path
import stat

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _read_template_file(template_dir, filename):
    """Read only readable regular files whose resolved path stays in the template."""
    path = Path(filename)
    if path.is_absolute():
        raise ValueError("path must be relative and contained in template root")
    root = template_dir.resolve()
    path = (root / path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("path must be contained in template root")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or not mode & 0o444:
        raise ValueError("must identify a readable regular file")
    return path.read_text()


def _check(template_dir):
    """Return (errors, agent_words, prompt_words); errors in check order."""
    template_dir = Path(template_dir)
    role = template_dir.name
    errors = []
    try:
        data = yaml.safe_load(_read_template_file(template_dir, "scion-agent.yaml"))
    except yaml.YAMLError as exc:
        return [f"{role}: scion-agent.yaml: invalid YAML: {exc}"], 0, 0
    except (OSError, ValueError, RuntimeError) as exc:
        return [f"{role}: scion-agent.yaml: must identify a readable regular file: {exc}"], 0, 0
    if not isinstance(data, dict):
        return [f"{role}: scion-agent.yaml: YAML must be a mapping"], 0, 0

    for key in data:
        if key.replace("-", "_") in ("harness", "harness_config", "default_harness_config"):
            errors.append(f"{role}: scion-agent.yaml: {key} belongs in settings or --harness-config")

    texts = {}
    filenames = {}
    for key, default in (("agent_instructions", "agents.md"),
                         ("system_prompt", "system-prompt.md")):
        # Older fixtures omit these keys; explicit null/invalid values never fall back.
        filename = data.get(key, default)
        if not isinstance(filename, str) or not filename.strip():
            errors.append(f"{role}: scion-agent.yaml: {key} must be a nonempty relative path")
            continue
        filenames[key] = filename
        try:
            texts[key] = _read_template_file(template_dir, filename)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{role}: scion-agent.yaml: {key} ({filename}): "
                          f"must identify a readable regular file in template root: {exc}")
    if errors:
        return errors, 0, 0
    instructions = texts["agent_instructions"]
    words = len(instructions.split())
    prompt_words = len(texts["system_prompt"].split())
    for required in ("## Completion Handoff", "agent:<dispatcher>", "--body-file",
                     "exit status", "retry once", "Completion delivery failed",
                     "status blocked", "status task_completed"):
        if required not in instructions:
            errors.append(f"{role}: missing {required}")
    if not ("sleep" in instructions and "poll" in instructions):
        errors.append(f"{role}: missing waiting rule")
    if role == "code-reviewer" and not (
            "re-review" in instructions and "REQUEST CHANGES" in instructions):
        errors.append(f"{role}: missing re-review/REQUEST CHANGES rules")
    return errors, words, prompt_words


def check_template(template_dir):
    """Return a list of budget/contract error strings for one template dir."""
    return _check(template_dir)[0]


def main():
    total = 0
    for config in sorted((ROOT / "templates").glob("*/scion-agent.yaml")):
        template_dir = config.parent
        role = template_dir.name
        errors, words, prompt_words = _check(template_dir)
        if errors:
            raise AssertionError(errors[0])
        total += words + prompt_words
        print(f"{role}: agents={words}, system={prompt_words} words")
    print(f"Total: {total} words (word counts, not tokenizer measurements)")


if __name__ == "__main__":
    main()
