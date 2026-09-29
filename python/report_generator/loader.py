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
import sys
from copy import deepcopy
from pathlib import Path

logger = logging.getLogger(__name__)


def get_resources_dir() -> str:
    """Resolve the resources directory path.

    In packaged (frozen) mode: set by Electron via INKCUBE_RESOURCES_DIR env var,
    or fallback to the directory containing the executable.
    In dev mode: ../../resources relative to this file (python/report_generator/ → resources/).
    """
    env_dir = os.environ.get("INKCUBE_RESOURCES_DIR")
    if env_dir and os.path.isdir(env_dir):
        return env_dir

    if getattr(sys, "frozen", False):
        # Packaged: Electron should set INKCUBE_RESOURCES_DIR
        # Fallback: same dir as executable
        base = os.path.dirname(sys.executable)
        return os.path.join(base, "resources")

    # Dev mode: python/report_generator/loader.py → ../../resources
    return str(Path(__file__).resolve().parent.parent.parent / "resources")


def get_templates_dir() -> str:
    """Get the preset templates directory path."""
    return os.path.join(get_resources_dir(), "templates")


def get_custom_templates_dir(data_dir: str) -> str:
    """Get the custom templates directory path in user data."""
    path = os.path.join(data_dir, "templates")
    os.makedirs(path, exist_ok=True)
    return path


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

    def _validate_template(self, template: dict) -> list[str]:
        """Validate a template against the schema. Returns list of errors (empty = valid).

        Uses basic structural validation without requiring jsonschema library.
        """
        errors = []
        # Required fields
        for field in ["template_id", "template_name", "version", "project_types", "outline"]:
            if field not in template:
                errors.append(f"Missing required field: {field}")

        if errors:
            return errors

        # Type checks
        if not isinstance(template.get("template_id"), str):
            errors.append("template_id must be string")
        if not isinstance(template.get("template_name"), str) or not template["template_name"]:
            errors.append("template_name must be non-empty string")
        if not isinstance(template.get("version"), str):
            errors.append("version must be string")
        if not isinstance(template.get("project_types"), list) or len(template["project_types"]) < 1:
            errors.append("project_types must be non-empty array")
        else:
            valid_types = {"investment", "recruitment", "general"}
            for pt in template["project_types"]:
                if pt not in valid_types:
                    errors.append(f"Invalid project_type: {pt}")
        if not isinstance(template.get("outline"), list) or len(template["outline"]) < 1:
            errors.append("outline must be non-empty array")

        # Optional field checks
        stage = template.get("stage")
        if stage is not None and stage not in ("before", "during", "after"):
            errors.append(f"Invalid stage: {stage}")

        output_format = template.get("output_format")
        if output_format is not None and output_format != "docx":
            errors.append(f"Invalid output_format: {output_format} (must be 'docx')")

        report_depth = template.get("report_depth")
        if report_depth is not None and report_depth not in ("full", "brief"):
            errors.append(f"Invalid report_depth: {report_depth}")

        # Validate outline structure recursively
        self._validate_outline_nodes(template.get("outline", []), errors)

        return errors

    def _validate_outline_nodes(self, nodes: list, errors: list, path: str = "outline") -> None:
        """Recursively validate outline node structure.

        Args:
            nodes: List of outline nodes to validate
            errors: Error list to append to
            path: Current path for error messages
        """
        for i, node in enumerate(nodes):
            node_path = f"{path}[{i}]"

            # Required fields
            if "section_id" not in node:
                errors.append(f"{node_path}: Missing required field 'section_id'")
            elif not isinstance(node["section_id"], str) or not node["section_id"]:
                errors.append(f"{node_path}: section_id must be non-empty string")

            if "title" not in node:
                errors.append(f"{node_path}: Missing required field 'title'")
            elif not isinstance(node["title"], str) or not node["title"]:
                errors.append(f"{node_path}: title must be non-empty string")

            # Optional field checks
            compose_mode = node.get("compose_mode", "standard")
            if compose_mode not in ("standard", "post_body"):
                errors.append(f"{node_path}: Invalid compose_mode: {compose_mode}")

            position = node.get("position")
            if position is not None and position not in ("front", "end"):
                errors.append(f"{node_path}: Invalid position: {position}")

            optional = node.get("optional", False)
            if not isinstance(optional, bool):
                errors.append(f"{node_path}: optional must be boolean")

            # post_body cannot have children
            if compose_mode == "post_body" and node.get("children"):
                errors.append(f"{node_path}: post_body chapters cannot have children")

            # Recursively validate children
            children = node.get("children", [])
            if children:
                if not isinstance(children, list):
                    errors.append(f"{node_path}: children must be array")
                else:
                    self._validate_outline_nodes(children, errors, f"{node_path}.children")

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
        for filename in os.listdir(custom_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(custom_dir, filename)
            template = self._load_template_file(filepath)
            if template is None:
                continue

            errors = self._validate_template(template)
            if errors:
                logger.warning("Custom template %s validation failed: %s", filename, errors)
                continue

            # Cache in DB
            tpl_id = template.get("template_id", filename.replace(".json", ""))
            self.db.save_template(
                template_id=tpl_id,
                name=template["template_name"],
                template_json=json.dumps(template, ensure_ascii=False),
                is_custom=True,
                version=template.get("version"),
            )
            loaded.append(template)

        logger.info("Loaded %d custom templates from %s", len(loaded), custom_dir)
        return loaded

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
                all_templates.append(tpl)
            except (json.JSONDecodeError, KeyError):
                logger.warning("Failed to parse cached template: %s", row.get("id"))

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
        if row:
            try:
                return json.loads(row["template_json"])
            except (json.JSONDecodeError, KeyError):
                return None
        return None

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
