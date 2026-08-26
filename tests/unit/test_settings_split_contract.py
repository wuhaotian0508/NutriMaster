from __future__ import annotations


def test_settings_exposes_focused_config_objects_while_preserving_flat_aliases(tmp_path):
    from nutrimaster.config.settings import Settings

    settings = Settings.from_env(
        {
            "NUTRIMASTER_ROOT": str(tmp_path),
            "OPENAI_API_KEY": "openai",
            "OPENAI_BASE_URL": "https://llm.example/v1",
            "MAIN_MODEL": "model",
            "EXPERIMENT_MODEL": "experiment-model",
            "JINA_API_KEY": "jina",
            "SUPABASE_URL": "https://supabase.example",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": "anon",
            "SMTP_USER": "mailer@example.com",
        }
    )

    assert settings.llm.api_key == "openai"
    assert settings.llm.base_url == "https://llm.example/v1"
    assert settings.llm.model == "model"
    assert settings.llm.experiment_model == "experiment-model"
    assert settings.experiment_model == "experiment-model"
    assert settings.auth.supabase_url == settings.supabase_url == "https://supabase.example"
    assert settings.auth.supabase_anon_key == settings.supabase_anon_key == "anon"
    assert settings.email.smtp_user == settings.smtp_user == "mailer@example.com"


def test_experiment_model_defaults_to_main_model(tmp_path):
    from nutrimaster.config.settings import Settings

    settings = Settings.from_env(
        {
            "NUTRIMASTER_ROOT": str(tmp_path),
            "MAIN_MODEL": "main-model",
        }
    )

    assert settings.experiment_model == "main-model"
    assert settings.llm.experiment_model == "main-model"
