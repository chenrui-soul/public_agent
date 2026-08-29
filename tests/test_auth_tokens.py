import pytest
from pydantic import SecretStr, ValidationError

from public_agent.auth import (
    APITokenCodec,
    AuthenticationError,
    PrincipalCreateRequest,
)


def test_api_token_codec_issues_high_entropy_secret_and_verifies_digest() -> None:
    codec = APITokenCodec(SecretStr("test-pepper-that-is-never-persisted"))

    material = codec.issue()
    prefix, digest = codec.parse(material.token)

    assert prefix == material.prefix
    assert len(material.prefix) == 12
    assert len(material.digest) == 32
    assert codec.matches(digest, material.digest)
    assert material.token.get_secret_value() not in repr(material)
    assert material.token.get_secret_value() not in str(material)


def test_api_token_codec_rejects_malformed_or_modified_tokens() -> None:
    codec = APITokenCodec("test-pepper")
    material = codec.issue()
    plaintext = material.token.get_secret_value()
    modified = plaintext[:-1] + ("A" if plaintext[-1] != "A" else "B")

    _, modified_digest = codec.parse(modified)

    assert not codec.matches(modified_digest, material.digest)
    for invalid in ("", "Bearer token", "public_agent_short.secret"):
        with pytest.raises(AuthenticationError, match="authentication required"):
            codec.parse(invalid)


def test_principal_create_request_normalizes_permissions_and_agent_scope() -> None:
    request = PrincipalCreateRequest(
        tenant_id=" tenant-a ",
        subject=" service-a ",
        display_name=" Service A ",
        permissions=("Knowledge:Read", "knowledge:read", "knowledge:write"),
        agent_ids=("support-agent", "support-agent"),
    )

    assert request.tenant_id == "tenant-a"
    assert request.permissions == ("knowledge:read", "knowledge:write")
    assert request.agent_ids == ("support-agent",)
    with pytest.raises(ValidationError, match="either all_agents or explicit agent_ids"):
        PrincipalCreateRequest(
            tenant_id="tenant-a",
            subject="service-a",
            display_name="Service A",
            permissions=("knowledge:read",),
        )
