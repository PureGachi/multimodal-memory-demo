import os
import pytest
from memory_app.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(("MEMORY_", "VISION_", "EMBEDDING_", "OLLAMA_", "OCR_", "TESSERACT_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def test_load_local_file_without_mutating_environment(tmp_path):
    (tmp_path / ".env").write_text(
        'MEMORY_EMBEDDINGS=none\nMEMORY_VISION=compatible\n'
        'VISION_BASE_URL=https://provider.example/v1\nVISION_MODEL=gpt-4o-mini\n'
        'VISION_API_KEY="test-secret"\n', encoding="utf-8")
    settings = Settings.from_env()
    assert settings.vision_api_key == "test-secret"
    assert settings.embeddings == "none" and settings.vision == "compatible"
    assert settings.vision_model == "gpt-4o-mini"
    assert "VISION_API_KEY" not in os.environ
    assert "test-secret" not in repr(settings)


def test_process_environment_has_priority_even_when_empty(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("VISION_API_KEY=file-secret\nMEMORY_EMBEDDINGS=none\n", encoding="utf-8")
    monkeypatch.setenv("VISION_API_KEY", "process-secret")
    monkeypatch.setenv("MEMORY_EMBEDDINGS", "ollama")
    assert Settings.from_env().vision_api_key == "process-secret"
    assert Settings.from_env().embeddings == "ollama"
    monkeypatch.setenv("VISION_API_KEY", "")
    assert Settings.from_env().vision_api_key == ""


def test_missing_file_preserves_defaults_and_does_not_search_parents(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MEMORY_EMBEDDINGS=none\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    assert Settings.from_env().embeddings == "ollama"


def test_custom_file_and_explicit_disable(tmp_path):
    target = tmp_path / "custom.env"
    target.write_text("MEMORY_EMBEDDINGS=none\n", encoding="utf-8")
    assert Settings.from_env(target).embeddings == "none"
    assert Settings.from_env(None).embeddings == "ollama"


def test_bom_literal_dollar_and_bare_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OTHER_SECRET", "must-not-expand")
    (tmp_path / ".env").write_text('MEMORY_EMBEDDINGS=none\nVISION_API_KEY="${OTHER_SECRET}"\nVISION_MODEL\n', encoding="utf-8-sig")
    settings = Settings.from_env()
    assert settings.embeddings == "none"
    assert settings.vision_api_key == "${OTHER_SECRET}"
    assert settings.vision_model == ""


def test_reloading_file_sees_changes(tmp_path):
    target = tmp_path / ".env"
    target.write_text("VISION_MODEL=first\n", encoding="utf-8")
    assert Settings.from_env().vision_model == "first"
    target.write_text("VISION_MODEL=second\n", encoding="utf-8")
    assert Settings.from_env().vision_model == "second"


def test_file_config_still_validated(tmp_path):
    (tmp_path / ".env").write_text("MEMORY_EMBEDDINGS=unknown\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MEMORY_EMBEDDINGS"):
        Settings.from_env()


def test_direct_settings_do_not_read_local_file(tmp_path):
    (tmp_path / ".env").write_text("MEMORY_EMBEDDINGS=none\n", encoding="utf-8")
    assert Settings().embeddings == "ollama"


def test_all_keys_excluded_from_repr():
    settings = Settings(api_key="service-secret", vision_api_key="vision-secret", embedding_api_key="embed-secret")
    assert "secret" not in repr(settings)


def test_vision_output_mode_from_file_and_validation(tmp_path):
    target = tmp_path / ".env"
    target.write_text("VISION_RESPONSE_FORMAT=json_object\n", encoding="utf-8")
    assert Settings.from_env().vision_response_format == "json_object"
    target.write_text("VISION_RESPONSE_FORMAT=text\n", encoding="utf-8")
    with pytest.raises(ValueError, match="VISION_RESPONSE_FORMAT"):
        Settings.from_env()
