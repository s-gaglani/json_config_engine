"""
Management command: load_oob_config

Simulates the release pipeline inserting a new OOB (out-of-box) config.

Usage:
    python manage.py load_oob_config \\
        --config-key invoice.form \\
        --release-version v2.0.0 \\
        --config-file /path/to/config.json
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.config_engine.models import ConfigInstance
from apps.config_engine.services import ConfigResolutionService


class Command(BaseCommand):
    help = "Load a new OOB config from a JSON file (immutable once created for a given key+version)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config-key",
            required=True,
            help="Logical config key, e.g. 'invoice.form'.",
        )
        parser.add_argument(
            "--release-version",
            required=True,
            help="Release version string, e.g. 'v2.0.0'.",
        )
        parser.add_argument(
            "--config-file",
            required=True,
            help="Path to a JSON file containing the config_json payload.",
        )

    def handle(self, *args, **options):
        config_key: str = options["config_key"]
        release_version: str = options["release_version"]
        config_file: str = options["config_file"]

        # ------------------------------------------------------------------
        # 1. Read and parse the JSON file
        # ------------------------------------------------------------------
        path = Path(config_file)
        if not path.exists():
            raise CommandError(f"Config file not found: {config_file}")

        try:
            config_json: dict = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in '{config_file}': {exc}") from exc

        if not isinstance(config_json, dict):
            raise CommandError("Config file must contain a JSON object (dict) at the top level.")

        # ------------------------------------------------------------------
        # 2. Guard: OOB configs are immutable — reject duplicate key+version
        # ------------------------------------------------------------------
        already_exists = ConfigInstance.objects.filter(
            config_key=config_key,
            scope_type="oob",
            release_version=release_version,
        ).exists()

        if already_exists:
            self.stdout.write(
                self.style.WARNING(
                    f"WARNING: An OOB config for key='{config_key}' "
                    f"version='{release_version}' already exists. "
                    "OOB configs are immutable — skipping."
                )
            )
            return

        # ------------------------------------------------------------------
        # 3. Deactivate the previously active OOB config for this key
        # ------------------------------------------------------------------
        deactivated = ConfigInstance.objects.filter(
            config_key=config_key,
            scope_type="oob",
            is_active=True,
        ).update(is_active=False)

        if deactivated:
            self.stdout.write(
                self.style.NOTICE(
                    f"Deactivated {deactivated} previously active OOB config(s) "
                    f"for key='{config_key}'."
                )
            )

        # ------------------------------------------------------------------
        # 4. Create the new active OOB ConfigInstance
        # ------------------------------------------------------------------
        instance = ConfigInstance.objects.create(
            config_key=config_key,
            scope_type="oob",
            scope_id=None,
            release_version=release_version,
            config_json=config_json,
            is_active=True,
            # OOB has no parent lineage
            base_config_id=None,
            base_release_version=None,
            base_config_hash=None,
            parent_config_instance_id=None,
        )

        # ------------------------------------------------------------------
        # 5. Invalidate cache so the new OOB is served immediately
        # ------------------------------------------------------------------
        ConfigResolutionService.invalidate_cache(config_key=config_key)

        # ------------------------------------------------------------------
        # 6. Success
        # ------------------------------------------------------------------
        self.stdout.write(
            self.style.SUCCESS(
                f"OOB config created successfully.\n"
                f"  id              : {instance.id}\n"
                f"  config_key      : {instance.config_key}\n"
                f"  release_version : {instance.release_version}\n"
                f"  is_active       : {instance.is_active}"
            )
        )
