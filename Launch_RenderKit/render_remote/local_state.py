import json
import os
import platform
import socket
from pathlib import Path

try:
	import bpy
except ImportError:
	bpy = None


_LOCAL_SETTINGS_FILENAME = "RenderKit_Settings.json"
_LOCAL_CACHE_DIRECTORY_NAME = "RenderKit_Cache"


def _fallback_blender_user_root():
	"""Return a sensible Blender user root when bpy helpers are unavailable."""
	home = Path.home()
	system_name = platform.system()
	if system_name == "Darwin":
		return home / "Library" / "Application Support" / "Blender"
	if system_name == "Windows":
		appdata = os.environ.get("APPDATA")
		if appdata:
			return Path(appdata) / "Blender Foundation" / "Blender"
		return home / "AppData" / "Roaming" / "Blender Foundation" / "Blender"
	return home / ".config" / "blender"


def blender_user_root():
	"""Return Blender's per-user storage root outside version-specific directories."""
	utils = getattr(bpy, "utils", None)
	resource_path = None
	if utils and hasattr(utils, "resource_path"):
		try:
			resource_path = utils.resource_path("USER")
		except Exception:
			resource_path = None

	if resource_path:
		path = Path(resource_path).expanduser()
		if path.name and path.name[0].isdigit():
			return str(path.parent)
		return str(path)

	return str(_fallback_blender_user_root())


def default_remote_cache_directory():
	"""Return the default local cache directory for Render Remote."""
	return str(Path(blender_user_root()) / _LOCAL_CACHE_DIRECTORY_NAME)


def local_settings_path():
	"""Return the host-local Render Remote settings file path."""
	return str(Path(blender_user_root()) / _LOCAL_SETTINGS_FILENAME)


def default_remote_node_name():
	"""Return a friendly local machine name without multicast suffixes."""
	for candidate in (platform.node(), socket.gethostname()):
		name = str(candidate or "").split(".", 1)[0].strip()
		if name:
			return name
	return "Render Target"


def load_local_settings():
	"""Load host-local Render Remote settings, tolerating absent or invalid files."""
	settings_path = Path(local_settings_path())
	try:
		with settings_path.open("r", encoding="utf-8") as handle:
			payload = json.load(handle)
	except FileNotFoundError:
		return {}
	except (OSError, ValueError, TypeError, json.JSONDecodeError):
		return {}

	return payload if isinstance(payload, dict) else {}


def save_local_settings(settings):
	"""Persist host-local Render Remote settings atomically."""
	payload = dict(settings or {})
	settings_path = Path(local_settings_path())
	settings_path.parent.mkdir(parents=True, exist_ok=True)
	temp_path = settings_path.with_name(f"{settings_path.name}.tmp")
	with temp_path.open("w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, sort_keys=True)
	temp_path.replace(settings_path)


def get_local_remote_mode():
	"""Return the persisted host-local operation mode."""
	mode = str(load_local_settings().get("remote_mode", "SOURCE")).upper()
	return mode if mode in {"SOURCE", "TARGET"} else "SOURCE"


def set_local_remote_mode(mode):
	"""Persist the host-local operation mode."""
	settings = load_local_settings()
	mode_value = str(mode or "SOURCE").upper()
	settings["remote_mode"] = mode_value if mode_value in {"SOURCE", "TARGET"} else "SOURCE"
	save_local_settings(settings)
