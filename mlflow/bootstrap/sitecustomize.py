"""Loaded by every MLflow Python worker (PYTHONPATH), including fresh gateway workers.

Setting LiteLLM options only in the CLI's parent process would not reliably reach
the gateway worker interpreters, so they are applied here.
"""

import litellm
from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig

# LiteLLM's GitHub Copilot adapter rewrites `system` messages as `assistant`
# history by default. That downgrades the role and workflow instructions agents
# send. Copilot supports system messages: keep their original role and content.
litellm.disable_copilot_system_to_assistant = True

# LiteLLM advertises reasoning_effort for Copilot Claude/Gemini models but its
# OpenAI param mapping then drops it, so the agent's thinking level never reaches
# Copilot. Keep it.
_get_supported_params = GithubCopilotConfig.get_supported_openai_params
_map_openai_params = GithubCopilotConfig.map_openai_params


def _supported_params_extended(self, model):
    params = list(_get_supported_params(self, model))
    if ("gemini" in model.lower() or "claude" in model.lower()) and "reasoning_effort" not in params:
        params.append("reasoning_effort")
    return params


def _map_with_reasoning_effort(self, non_default_params, optional_params, model, drop_params):
    mapped = _map_openai_params(self, non_default_params, optional_params, model, drop_params)
    effort = non_default_params.get("reasoning_effort")
    if effort and "reasoning_effort" in self.get_supported_openai_params(model):
        mapped["reasoning_effort"] = effort
    return mapped


GithubCopilotConfig.get_supported_openai_params = _supported_params_extended
GithubCopilotConfig.map_openai_params = _map_with_reasoning_effort

# Group gateway traces per orchestration session (see gateway_session.py).
import gateway_session  # noqa: E402

gateway_session.install()
