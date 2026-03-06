from datetime import datetime, timezone
from pathlib import Path

from deepbot.config import AppConfig
from deepbot.security import Incident, IncidentSummarizer


def _make_config() -> AppConfig:
    return AppConfig(
        discord_bot_token="token",
        strands_model_provider="",
        strands_model_config={},
        openai_base_url=None,
        agent_md_path=Path("config/AGENT.md"),
        session_max_turns=10,
        session_ttl_minutes=30,
        auto_reply_all=True,
        auto_thread_enabled=False,
        auto_thread_mode="keyword",
        auto_thread_channel_ids=(),
        auto_thread_trigger_keywords=(),
        auto_thread_archive_minutes=1440,
        auto_thread_rename_from_reply=True,
        agent_timeout_seconds=45,
        bot_fallback_message="fallback",
        bot_processing_message="processing",
        log_level="INFO",
        dangerous_tools_enabled=False,
        enabled_dangerous_tools=(),
        shell_srt_enforced=True,
        shell_srt_settings_path="/app/config/srt-settings.json",
        shell_deny_path_prefixes=("/app/.env",),
        tool_read_roots=("/workspace",),
        tool_write_roots=("/workspace",),
        auth_passphrase="",
        auth_required=False,
        auth_idle_timeout_minutes=15,
        auth_window_minutes=10,
        auth_max_retries=3,
        auth_lock_minutes=30,
        auth_command="/auth",
        defender_enabled=True,
        defender_default_mode="warn",
        defender_block_threshold=0.95,
        defender_warn_threshold=0.35,
        defender_sanitize_mode="full-redact",
        attachment_allowed_hosts=("cdn.discordapp.com",),
        cron_enabled=False,
        cron_jobs_dir=Path("/workspace/jobs"),
        cron_default_timezone="Asia/Tokyo",
        cron_poll_seconds=15,
        cron_busy_message="busy",
        claude_subagent_enabled=False,
        claude_subagent_command="claude",
        claude_subagent_workdir="/workspace",
        claude_subagent_timeout_seconds=300,
        claude_subagent_model=None,
        claude_subagent_skip_permissions=False,
        claude_subagent_transport="direct",
        claude_subagent_sidecar_url="http://claude-runner:8787/v1/run",
        claude_subagent_sidecar_token="",
        claude_hooks_enabled=False,
        claude_hooks_timeout_ms=5000,
        claude_hooks_fail_mode="open",
        claude_hooks_settings_paths=(".claude/settings.json",),
        security_enabled=True,
        security_alert_bind_host="127.0.0.1",
        security_alert_bind_port=8088,
        security_rules_path=Path("config/security/detection-rules.yaml"),
        security_allowlist=(),
        security_state_dir=Path(".state/test"),
        security_alert_channel_id="123",
        security_port_monitor_enabled=False,
        security_port_monitor_interval_seconds=60,
        security_port_monitor_protocols=("tcp",),
        security_port_monitor_exclude_ports=(),
        security_resource_monitor_enabled=False,
        security_resource_monitor_interval_seconds=60,
        security_cpu_load_percent_threshold=85,
        security_memory_percent_threshold=90,
        security_disk_percent_threshold=90,
    )


def test_summarizer_falls_back_when_agent_unavailable() -> None:
    summarizer = IncidentSummarizer(_make_config())
    summarizer._agent = None  # noqa: SLF001
    incident = Incident(
        category="mystery_security_event",
        severity="high",
        description="Unknown event",
        count=2,
        window_seconds=120,
        first_seen=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        last_seen=datetime(2026, 3, 6, 12, 1, tzinfo=timezone.utc),
        src_ip="203.0.113.10",
        username="root",
        service="sshd",
        hostname="host1",
        message_samples=["mystery event happened"],
        raw_context=[{"message": "mystery event happened"}],
    )

    notification = summarizer.summarize(incident)

    assert notification.title
    assert "mystery_security_event" in notification.summary
    assert len(notification.recommended_actions) == 3
