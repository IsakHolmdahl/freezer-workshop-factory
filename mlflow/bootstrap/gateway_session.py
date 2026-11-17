"""Group MLflow AI Gateway traces into sessions from request headers.

MLflow groups traces by the ``mlflow.trace.session`` and ``mlflow.trace.user``
metadata keys, but the gateway (MLflow 3.17) does not read them from requests.
This hook copies two caller-supplied headers into that metadata:

- ``X-MLflow-Session-Id``  -> ``mlflow.trace.session`` (one orchestration)
- ``X-MLflow-Trace-User``  -> ``mlflow.trace.user``    (the acting agent)

It wraps ``mlflow.server.gateway_api._get_user_metadata``, which every gateway
handler calls by module-global name, after that module is first imported. If a
future MLflow drops the function, the hook does nothing.
"""

import importlib.abc
import re
import sys

TARGET = "mlflow.server.gateway_api"
ASSISTANT_TARGET = "mlflow.server.assistant.api"
OPENAI_COMPAT_TARGET = "mlflow.assistant.providers.openai_compatible"
HEADERS = {
    "x-mlflow-session-id": "mlflow.trace.session",
    "x-mlflow-trace-user": "mlflow.trace.user",
}
# Safe identifier only; anything else is ignored rather than recorded.
VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")


def session_metadata(headers):
    """Return trace metadata for the recognised headers with valid values."""
    found = {}
    for header, key in HEADERS.items():
        value = (headers.get(header) or "").strip()
        if VALUE.fullmatch(value):
            found[key] = value
    return found


def patch(module):
    original = getattr(module, "_get_user_metadata", None)
    if original is None or getattr(original, "_gateway_session", False):
        return

    def _get_user_metadata(request):
        metadata = original(request)
        try:
            metadata.update(session_metadata(request.headers))
        except Exception:  # Never fail a model call because of trace grouping.
            pass
        return metadata

    _get_user_metadata._gateway_session = True
    module._get_user_metadata = _get_user_metadata


def patch_assistant(module):
    """Allow MLflow Assistant UI config/chat from Docker bridge networks on localhost."""
    if getattr(module, "_asf_assistant_patched", False):
        return
    module._is_localhost = lambda request: True
    module._asf_assistant_patched = True


def patch_openai_compatible(module):
    """Fix sparse tool_call index handling when Anthropic/Copilot emits index >= 1 after text blocks."""
    if getattr(module, "_asf_openai_compat_patched", False):
        return

    def _merge_tool_call_chunk(accumulator, chunk):
        idx = chunk.get("index", 0)
        # Map each distinct streamed tool_call index to a dense entry so leading
        # content block indices (e.g. text block at index=0, tool_use at index=1)
        # never insert blank {"name": ""} placeholder tool_calls.
        entry = next((item for item in accumulator if item.get("_stream_index") == idx), None)
        if entry is None:
            entry = {"_stream_index": idx, "id": "", "function": {"name": "", "arguments": ""}}
            accumulator.append(entry)
        if call_id := chunk.get("id"):
            entry["id"] = call_id
        fn = chunk.get("function") or {}
        if name := fn.get("name"):
            entry["function"]["name"] = name
        if args := fn.get("arguments"):
            entry["function"]["arguments"] += args

    module._merge_tool_call_chunk = _merge_tool_call_chunk
    module._asf_openai_compat_patched = True


class _PatchOnImport(importlib.abc.MetaPathFinder):
    """Patch the gateway and assistant modules right after they execute, before any request."""

    def find_spec(self, name, path, target=None):
        if name not in (TARGET, ASSISTANT_TARGET, OPENAI_COMPAT_TARGET):
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        else:
            return None
        loader = spec.loader
        exec_module = loader.exec_module

        def exec_and_patch(module):
            exec_module(module)
            if name == TARGET:
                patch(module)
            elif name == ASSISTANT_TARGET:
                patch_assistant(module)
            elif name == OPENAI_COMPAT_TARGET:
                patch_openai_compatible(module)

        loader.exec_module = exec_and_patch
        return spec


def install():
    if TARGET in sys.modules:
        patch(sys.modules[TARGET])
    if ASSISTANT_TARGET in sys.modules:
        patch_assistant(sys.modules[ASSISTANT_TARGET])
    if OPENAI_COMPAT_TARGET in sys.modules:
        patch_openai_compatible(sys.modules[OPENAI_COMPAT_TARGET])
    if not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _PatchOnImport())
