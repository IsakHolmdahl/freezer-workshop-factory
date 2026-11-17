"""Repository prompt normalization without exposing token-like invalid input."""
import pytest
from steps.secrets import parse_repositories


def test_clone_urls_and_names_are_normalized_and_deduplicated():
    assert parse_repositories("owner/repo, https://github.com/owner/repo.git git@github.com:team/service.git ssh://git@github.com/team/other.git") == [
        "owner/repo", "team/service", "team/other"]


@pytest.mark.parametrize("value", ["", "repo", "https://example.com/owner/repo", "https://token@github.com/owner/repo", "ghp_secret"])
def test_invalid_input_is_rejected_without_echoing_it(value):
    with pytest.raises(ValueError) as error:
        parse_repositories(value)
    assert "ghp_secret" not in str(error.value)
    assert "token@" not in str(error.value)
