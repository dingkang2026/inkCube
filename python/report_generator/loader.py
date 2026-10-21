"""python/report_generator/loader.py — Template loader.

Responsibilities:
- Scan preset templates from resources/templates/
- Validate each template against _schema.json
- Cache preset templates in DB (templates table)
- Load custom templates from user data directory
- Provide unified list of all templates (preset + custom)
"""
import json
import logging
import os
import shutil
from copy import deepcopy

from paths import get_templates_dir  # 资源目录单一来源（paths.py 收敛）

logger = logging.getLogger(__name__)


def get_custom_templates_dir(data_dir: str) -> str:
    """Get the custom templates directory path in user data."""
    path = os.path.join(data_dir, "templates")
    os.makedirs(path, exist_ok=True)
    return path


# ── Template schema versioning & migration framework ──
# Current schema version. Bump when the template JSON shape changes and add a
# migration function to MIGRATIONS so older templates upgrade automatically.
CURRENT_SCHEMA_VERSION = 2

# Fields removed in v2 (present only in legacy v1 templates).
# NOTE: top-level `variables` is NOT obsolete — v1 flat strings are *converted*
# to v2 structured user variables (see _convert_v1_variables); only `terminology`
# is dropped at top level. Node-level `data_sources` stays obsolete (it moved to
# the variable level in v2). `variables` must NOT be used as a v1 detection
# signal because it exists in both v1 and v2 (differing only by shape).
_OBSOLETE_TOP_LEVEL_V1 = {"terminology"}
_OBSOLETE_NODE_FIELDS_V1 = {
    "word_count", "query_hints", "notes", "must_cite", "data_sources", "promote_subhead",
}

# Node fields dropped *within* v2: the foreach list-expansion capability was
# removed in favor of declarative {{}} injection (docs/2.5report-design.md §2.1.7).
# Kept SEPARATE from _OBSOLETE_NODE_FIELDS_V1 so a v2 template carrying a stale
# `foreach` is not misdetected as v1 by _node_is_v1. Stripped on every load to
# heal stale/hand-written nodes without a schema version bump.
_OBSOLETE_NODE_FIELDS_V2 = {"foreach", "item_as"}


def _node_is_v1(node) -> bool:
    """Heuristic: does this outline node use the legacy v1 shape?"""
    if not isinstance(node, dict):
        return False
    if "id" in node and "section_id" not in node:
        return True
    if _OBSOLETE_NODE_FIELDS_V1 & set(node.keys()):
        return True
    return any(_node_is_v1(c) for c in node.get("children", []) if isinstance(c, dict))


def _looks_like_v1(data) -> bool:
    """Heuristic: does this template use the legacy v1 shape (no schema_version)?"""
    if not isinstance(data, dict):
        return False
    if _OBSOLETE_TOP_LEVEL_V1 & set(data.keys()):
        return True
    if data.get("output_format") not in (None, "docx"):
        return True
    return any(_node_is_v1(n) for n in data.get("outline", []) if isinstance(n, dict))


def detect_schema_version(data) -> int:
    """Return the schema version of a template dict.

    Prefers the explicit schema_version field; falls back to structural
    detection for legacy templates written before versioning existed.
    """
    if not isinstance(data, dict):
        return CURRENT_SCHEMA_VERSION
    explicit = data.get("schema_version")
    if isinstance(explicit, int):
        return explicit
    return 1 if _looks_like_v1(data) else CURRENT_SCHEMA_VERSION


def _migrate_node_v1_to_v2(node: dict) -> dict:
    out = dict(node)
    if "section_id" not in out and "id" in out:
        out["section_id"] = out.pop("id")
    else:
        out.pop("id", None)
    for key in _OBSOLETE_NODE_FIELDS_V1:
        out.pop(key, None)
    if isinstance(out.get("children"), list):
        out["children"] = [_migrate_node_v1_to_v2(c) for c in out["children"] if isinstance(c, dict)]
    return out


def _convert_v1_variables(raw) -> list[dict]:
    """Convert v1 flat variable declarations → v2 structured user variables.

    v1 shape: ["公司名", "行业"] (placeholder names) — or already-structured
    dicts on idempotent re-migration. v2 shape: structured variable objects
    (see _schema.json $defs/variable). Non-string / nameless entries are dropped.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "source": "user", "label": name, "required": True})
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"].strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item.setdefault("source", "user")
            out.append(item)
    return out


def _migrate_v1_to_v2(data: dict) -> dict:
    """v1 (id-based, obsolete fields) → v2 (section_id-based, closed schema)."""
    out = dict(data)
    for key in _OBSOLETE_TOP_LEVEL_V1:
        out.pop(key, None)
    # v1 flat `variables` → v2 structured user variables (converted, not dropped)
    converted_vars = _convert_v1_variables(out.pop("variables", None))
    if converted_vars:
        out["variables"] = converted_vars
    if out.get("output_format") != "docx":
        out["output_format"] = "docx"
    out["outline"] = [
        _migrate_node_v1_to_v2(n) for n in out.get("outline", []) if isinstance(n, dict)
    ]
    return out


# Registry of chained migrations: {from_version: fn(data)->data}.
MIGRATIONS = {1: _migrate_v1_to_v2}


def _strip_obsolete_node_fields(node: dict) -> dict:
    """Return a copy of a v2 node with obsolete fields (foreach/item_as) removed.

    Recurses into children. Runs on every load so stale/hand-written v2 nodes
    that predate the foreach removal are healed before schema validation.
    """
    out = dict(node)
    for key in _OBSOLETE_NODE_FIELDS_V2:
        out.pop(key, None)
    if isinstance(out.get("children"), list):
        out["children"] = [
            _strip_obsolete_node_fields(c) for c in out["children"] if isinstance(c, dict)
        ]
    return out


def _strip_obsolete_nodes(data: dict) -> dict:
    """Heal a template's outline by stripping v2-obsolete node fields.

    Returns a new dict (does not mutate the caller's original). No-op when the
    template has no list-shaped outline.
    """
    if not isinstance(data.get("outline"), list):
        return data
    out = dict(data)
    out["outline"] = [
        _strip_obsolete_node_fields(n) for n in out["outline"] if isinstance(n, dict)
    ]
    return out


def migrate_template(data):
    """Upgrade a template dict to CURRENT_SCHEMA_VERSION.

    Idempotent: already-current templates pass through unchanged (stamped),
    except that v2-obsolete node fields (foreach/item_as) are always stripped
    to heal stale templates. Returns None if the template is from an
    unknown/newer version or malformed.
    """
    if not isinstance(data, dict):
        return None
    version = detect_schema_version(data)
    if version > CURRENT_SCHEMA_VERSION:
        return None
    while version < CURRENT_SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            return None
        data = step(data)
        version += 1
        data["schema_version"] = version
    # Always heal v2-obsolete node fields (foreach/item_as), even for already-v2
    # templates, so stale/hand-written nodes pass the closed schema validation.
    data = _strip_obsolete_nodes(data)
    data.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
    return data


class TemplateLoader:
    """Loads and manages report templates.

    Preset templates: read-only, bundled with the app in resources/templates/.
    Custom templates: user-created, stored in {data_dir}/templates/.
    """

    def __init__(self, db, data_dir: str):
        self.db = db
        self.data_dir = data_dir
        self._preset_dir = get_templates_dir()
        self._custom_dir = get_custom_templates_dir(data_dir)
        self._schema = None
        self._validator = None
        # {template_id: reason} for custom templates needing user upgrade/repair
        self._custom_problems: dict[str, str] = {}

    def _load_schema(self) -> dict | None:
        """Load the JSON Schema for template validation."""
        if self._schema is not None:
            return self._schema
        schema_path = os.path.join(self._preset_dir, "_schema.json")
        if not os.path.isfile(schema_path):
            logger.warning("Template schema not found: %s", schema_path)
            return None
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                self._schema = json.load(f)
            return self._schema
        except Exception as e:
            logger.error("Failed to load template schema: %s", e)
            return None

    def _get_validator(self):
        """Lazily build a Draft7 validator from _schema.json (single source of truth)."""
        if self._validator is not None:
            return self._validator
        schema = self._load_schema()
        if schema is None:
            return None
        from jsonschema import Draft7Validator
        self._validator = Draft7Validator(schema)
        return self._validator

    def _validate_template(self, template: dict) -> list[str]:
        """Validate a template against _schema.json via jsonschema.

        Returns a list of error strings (empty = valid). _schema.json is the
        single source of truth for template structure (no hand-written rules).
        """
        validator = self._get_validator()
        if validator is None:
            return ["Template schema (_schema.json) unavailable; cannot validate"]
        errors = []
        for err in sorted(validator.iter_errors(template), key=lambda e: list(e.path)):
            loc = "/".join(str(p) for p in err.path) or "<root>"
            errors.append(f"{loc}: {err.message}")
        return errors

    def validate_template(self, template: dict) -> list[str]:
        """Public wrapper: validate a template, returning error list (empty = valid)."""
        return self._validate_template(template)

    def _load_manifest(self) -> dict | None:
        """Load the manifest.json file."""
        manifest_path = os.path.join(self._preset_dir, "manifest.json")
        if not os.path.isfile(manifest_path):
            logger.warning("Template manifest not found: %s", manifest_path)
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load template manifest: %s", e)
            return None

    def _load_template_file(self, filepath: str) -> dict | None:
        """Load a single template JSON file."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load template %s: %s", filepath, e)
            return None

    def load_preset_templates(self) -> list[dict]:
        """Scan and load all preset templates from resources/templates/.

        Validates each template, caches in DB. Returns list of valid templates.
        """
        manifest = self._load_manifest()
        if not manifest:
            logger.error("Cannot load preset templates: manifest not found")
            return []

        templates_info = manifest.get("templates", {})
        loaded = []

        for tpl_id, info in templates_info.items():
            filename = info.get("file", f"{tpl_id}.json")
            filepath = os.path.join(self._preset_dir, filename)

            if not os.path.isfile(filepath):
                logger.warning("Preset template file not found: %s", filepath)
                continue

            template = self._load_template_file(filepath)
            if template is None:
                continue

            # Migrate legacy schema before validation
            template = migrate_template(template)
            if template is None:
                logger.error("Preset template %s migration failed: %s", tpl_id, filepath)
                continue

            # Validate
            errors = self._validate_template(template)
            if errors:
                logger.error("Preset template %s validation failed: %s", tpl_id, errors)
                continue

            # Cache in DB
            self.db.save_template(
                template_id=tpl_id,
                name=template["template_name"],
                template_json=json.dumps(template, ensure_ascii=False),
                is_custom=False,
                version=template.get("version"),
            )
            loaded.append(template)

        logger.info("Loaded %d preset templates from %s", len(loaded), self._preset_dir)
        return loaded

    def load_custom_templates(self) -> list[dict]:
        """Load all custom templates from user data directory."""
        custom_dir = self._custom_dir
        if not os.path.isdir(custom_dir):
            return []

        loaded = []
        self._custom_problems = {}
        for filename in os.listdir(custom_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(custom_dir, filename)
            stem = filename.replace(".json", "")
            raw = self._load_template_file(filepath)
            if raw is None:
                # Broken file: keep on disk, flag for user attention (never delete user data)
                self._custom_problems[stem] = "file parse failed"
                logger.warning("Custom template %s is broken (parse failed); kept on disk, flagged for upgrade", filename)
                continue

            tpl_id = raw.get("template_id") or stem

            # Migrate legacy schema before validation
            template = migrate_template(raw)
            if template is None:
                self._custom_problems[tpl_id] = "unsupported schema version"
                logger.warning("Custom template %s cannot be migrated; kept on disk, flagged for upgrade", filename)
                continue

            errors = self._validate_template(template)
            if errors:
                self._custom_problems[tpl_id] = "; ".join(errors[:3])
                logger.warning("Custom template %s validation failed: %s; kept on disk, flagged for upgrade", filename, errors)
                continue

            # Auto-upgrade in place: write migrated JSON back to disk when changed
            if template != raw:
                try:
                    # One-time backup of the original before overwriting (never clobber an existing .bak)
                    bak_path = filepath + ".bak"
                    if not os.path.exists(bak_path):
                        shutil.copy2(filepath, bak_path)
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(template, f, ensure_ascii=False, indent=2)
                    logger.info("Custom template %s auto-upgraded to schema v%d (backup: %s.bak)", filename, CURRENT_SCHEMA_VERSION, filename)
                except Exception as e:
                    logger.error("Failed to write back upgraded custom template %s: %s", filename, e)

            # Cache in DB
            self.db.save_template(
                template_id=tpl_id,
                name=template["template_name"],
                template_json=json.dumps(template, ensure_ascii=False),
                is_custom=True,
                version=template.get("version"),
            )
            loaded.append(template)

        logger.info("Loaded %d custom templates from %s (%d flagged)", len(loaded), custom_dir, len(self._custom_problems))
        return loaded

    def list_custom_problems(self) -> dict:
        """Return {template_id: reason} for custom templates needing user upgrade/repair."""
        return dict(self._custom_problems)

    def get_all_templates(
        self,
        project_type: str | None = None,
        stage: str | None = None,
    ) -> list[dict]:
        """Get all templates (preset + custom), optionally filtered.

        Args:
            project_type: Filter by project type (template's project_types contains it).
            stage: Filter by stage (template's stage matches, or template has no stage).

        Returns:
            List of template dicts with full content.
        """
        all_templates = []

        # Load from DB (cached)
        db_templates = self.db.list_templates()
        for row in db_templates:
            try:
                tpl = json.loads(row["template_json"])
            except (json.JSONDecodeError, KeyError):
                logger.warning("Failed to parse cached template: %s", row.get("id"))
                continue
            tpl = migrate_template(tpl)
            if tpl is None:
                logger.warning("Cached template %s migration failed; skipped", row.get("id"))
                continue
            all_templates.append(tpl)

        # Filter by project_type
        if project_type:
            all_templates = [
                t for t in all_templates
                if project_type in t.get("project_types", [])
            ]

        # Filter by stage
        if stage:
            all_templates = [
                t for t in all_templates
                if t.get("stage") == stage or t.get("stage") is None
            ]

        return all_templates

    def get_template(self, template_id: str) -> dict | None:
        """Get a single template by ID."""
        row = self.db.get_template(template_id)
        if not row:
            return None
        try:
            tpl = json.loads(row["template_json"])
        except (json.JSONDecodeError, KeyError):
            return None
        migrated = migrate_template(tpl)
        if migrated is None:
            return None
        if migrated != tpl:
            # Self-heal stale DB cache so future reads are already current
            self.db.save_template(
                template_id=migrated.get("template_id", template_id),
                name=migrated.get("template_name", ""),
                template_json=json.dumps(migrated, ensure_ascii=False),
                is_custom=bool(row.get("is_custom")),
                version=migrated.get("version"),
            )
        return migrated

    def copy_template(self, source_id: str, new_id: str, new_name: str) -> dict | None:
        """Copy a preset template as a custom template.

        Args:
            source_id: Source template ID.
            new_id: New template ID for the copy.
            new_name: New template name.

        Returns:
            The copied template dict, or None if source not found.
        """
        source = self.get_template(source_id)
        if source is None:
            return None

        copied = deepcopy(source)
        copied["template_id"] = new_id
        copied["template_name"] = new_name
        copied.setdefault("schema_version", CURRENT_SCHEMA_VERSION)

        # Save to custom templates directory
        filepath = os.path.join(self._custom_dir, f"{new_id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(copied, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save copied template: %s", e)
            return None

        # Cache in DB
        self.db.save_template(
            template_id=new_id,
            name=new_name,
            template_json=json.dumps(copied, ensure_ascii=False),
            is_custom=True,
            version=copied.get("version"),
        )
        return copied

    def save_custom_template(self, template: dict) -> dict | None:
        """Save a custom template (create or update).

        Args:
            template: Template dict (must pass validation).

        Returns:
            The saved template dict, or None if validation fails.
        """
        # Normalize/migrate + stamp current schema version before validation
        migrated = migrate_template(template)
        if migrated is not None:
            template = migrated
        template.setdefault("schema_version", CURRENT_SCHEMA_VERSION)

        errors = self._validate_template(template)
        if errors:
            logger.error("Custom template validation failed: %s", errors)
            return None

        tpl_id = template["template_id"]

        # Save to file
        filepath = os.path.join(self._custom_dir, f"{tpl_id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(template, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save custom template: %s", e)
            return None

        # Cache in DB
        self.db.save_template(
            template_id=tpl_id,
            name=template["template_name"],
            template_json=json.dumps(template, ensure_ascii=False),
            is_custom=True,
            version=template.get("version"),
        )
        return template

    def delete_custom_template(self, template_id: str) -> bool:
        """Delete a custom template.

        Only custom templates can be deleted. Preset templates are read-only.

        Returns:
            True if deleted, False if not found or is preset.
        """
        row = self.db.get_template(template_id)
        if not row:
            return False
        if not row.get("is_custom"):
            logger.warning("Cannot delete preset template: %s", template_id)
            return False

        # Delete file
        filepath = os.path.join(self._custom_dir, f"{template_id}.json")
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logger.warning("Failed to delete custom template file: %s", e)

        # Delete from DB
        self.db.delete_template(template_id)
        return True

    def refresh(self) -> dict:
        """Refresh all templates: reload preset + custom, update DB cache.

        Returns:
            Summary dict with counts.
        """
        preset = self.load_preset_templates()
        custom = self.load_custom_templates()
        return {
            "preset_count": len(preset),
            "custom_count": len(custom),
            "total": len(preset) + len(custom),
        }
