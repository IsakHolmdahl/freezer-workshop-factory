"""Build-time checks for the LiteLLM patches sitecustomize loads in gateway workers."""
import litellm
import mcp  # noqa: F401
import gateway_session  # noqa: F401
from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig


def main():
    assert litellm.disable_copilot_system_to_assistant is True
    config = GithubCopilotConfig()
    for model in ("github_copilot/gemini-2.5-pro", "github_copilot/claude-sonnet-4"):
        assert "reasoning_effort" in config.get_supported_openai_params(model)
        mapped = config.map_openai_params({"reasoning_effort": "high"}, {}, model, True)
        assert mapped["reasoning_effort"] == "high"
    print("verify_runtime: LiteLLM Copilot patches active")


if __name__ == "__main__":
    main()
