"""Obtain a Copilot API key at startup; prints a device-login code to the log if no token is stored yet."""

from litellm.llms.github_copilot.authenticator import Authenticator

Authenticator().get_api_key()
print("copilot_login: Copilot API key ready")
