"""Minimal helpers for the AFS dolt service (only what dolt.py uses).
Stdlib only; diagnostics never echo subprocess output."""
import os
from pathlib import Path
import subprocess
import uuid


class Failure(Exception): pass
class Missing(Failure): pass


def run(argv, *, env=None, input=None, timeout=120):
    try: r = subprocess.run(argv, input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    except FileNotFoundError: raise Missing('required executable missing') from None
    except subprocess.TimeoutExpired: raise Failure('operation deadline exceeded; inspect isolated target') from None
    if r.returncode: raise Failure('operation failed (exit %d); inspect private target' % r.returncode)
    return r.stdout


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or any(a.is_symlink() for a in path.parents): raise Failure('refusing symlink output')
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as f: f.write(value); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
