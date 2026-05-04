import os
import re
from urllib.parse import unquote
from pathlib import Path, PurePosixPath
from .constants import INPUT_MANIFEST_FILENAME

class PathSecurityError(Exception):
	"""Raised when a network-supplied path escapes an allowed root"""
	pass

class FileFilter:
	"""Centralized file filtering logic"""

	# Files that should never be synced (OS, temp, backup files) - matched by extension
	IGNORE_EXTENSIONS = {
		'.tmp', '.temp', '.log', '.lock', '.bak', '.backup',
		'.blend1', '.blend2', '.blend3',  # Blender backups
	}

	# Files that should never be synced - matched by exact lowercase filename
	IGNORE_FILENAMES = {
		'.ds_store', '._.ds_store',  # macOS metadata
		'thumbs.db', 'ehthumbs.db', 'desktop.ini',  # Windows thumbnail/config files
		'.directory',  # Linux KDE metadata
	}

	# Directories that should never be synced
	IGNORE_DIRECTORIES = {
		'__pycache__', '.git', '.svn', '.hg',
		'node_modules', '.cache',
		'temp', 'tmp', '.tmp',
	}

	# Common render output patterns (these are outputs, not dependencies)
	RENDER_OUTPUT_PATTERNS = {
		'render', 'renders', 'output', 'outputs', 'frames', 'images',
		'animation', 'anim', 'sequence', 'comp', 'compositing'
	}

	@classmethod
	def should_ignore_file(cls, file_path, is_dependency_scan=False):
		"""Check if a file should be ignored during scanning/syncing"""
		file_name = os.path.basename(file_path).lower()
		file_ext = os.path.splitext(file_name)[1].lower()

		# Always ignore certain filenames and extensions
		if file_name in cls.IGNORE_FILENAMES or file_ext in cls.IGNORE_EXTENSIONS:
			return True

		# Check if it's in an ignored directory
		path_parts = Path(file_path).parts
		for part in path_parts:
			if part.lower() in cls.IGNORE_DIRECTORIES:
				return True

		return False

	@classmethod
	def is_likely_render_output(cls, file_path, project_root=None):
		"""Check if a file is likely a render output (not a dependency)"""
		file_name = os.path.basename(file_path).lower()
		file_ext = os.path.splitext(file_name)[1].lower()

		# Image/video files in render-like directories
		if file_ext in {'.png', '.jpg', '.jpeg', '.exr', '.tif', '.tiff', '.mp4', '.mov', '.avi'}:
			path_lower = file_path.lower()
			for pattern in cls.RENDER_OUTPUT_PATTERNS:
				if pattern in path_lower:
					return True

			# Check for numbered sequences (typical render output)
			import re
			if re.search(r'\d{3,6}\.(png|jpg|jpeg|exr|tif|tiff)$', file_name):
				return True

		return False

def normalize_relative_path(relative_path):
	"""Normalize a project-relative POSIX path and reject traversal"""
	if relative_path is None:
		raise PathSecurityError("Relative path is required")

	path_text = str(relative_path).replace('\\', '/').strip()
	if not path_text:
		raise PathSecurityError("Relative path is required")
	if '\x00' in path_text:
		raise PathSecurityError("Invalid relative path")
	if re.match(r'^[A-Za-z]:', path_text):
		raise PathSecurityError("Absolute paths are not allowed")

	decoded_text = path_text
	for _ in range(3):
		next_decoded = unquote(decoded_text)
		if next_decoded == decoded_text:
			break
		decoded_text = next_decoded

	if decoded_text != path_text:
		decoded_check = decoded_text.replace('\\', '/')
		if '\x00' in decoded_check:
			raise PathSecurityError("Invalid relative path")
		if re.match(r'^[A-Za-z]:', decoded_check):
			raise PathSecurityError("Absolute paths are not allowed")
		decoded_path = PurePosixPath(decoded_check)
		if decoded_path.is_absolute():
			raise PathSecurityError("Absolute paths are not allowed")
		if any(part == '..' for part in decoded_path.parts):
			raise PathSecurityError("Path traversal is not allowed")

	path = PurePosixPath(path_text)
	if path.is_absolute():
		raise PathSecurityError("Absolute paths are not allowed")

	parts = []
	for part in path.parts:
		if part in ('', '.'):
			continue
		if part == '..':
			raise PathSecurityError("Path traversal is not allowed")
		parts.append(part)

	if not parts:
		raise PathSecurityError("Relative path is required")
	return '/'.join(parts)

def is_reserved_input_manifest_path(relative_path):
	"""Return True for internal manifest files that must not be synced or deleted as inputs"""
	manifest_paths = (
		INPUT_MANIFEST_FILENAME,
		f"{INPUT_MANIFEST_FILENAME}.tmp",
	)
	return any(
		relative_path == manifest_path or relative_path.startswith(f"{manifest_path}/")
		for manifest_path in manifest_paths
	)

def resolve_under_root(root_path, relative_path):
	"""Resolve a normalized relative path under an allowed root"""
	root = Path(root_path).expanduser().resolve()
	rel_path = normalize_relative_path(relative_path)
	candidate = root.joinpath(*rel_path.split('/')).resolve(strict=False)
	if not candidate.is_relative_to(root):
		raise PathSecurityError("Path escapes allowed root")
	# Belt-and-suspenders: ensure no existing symlink in the path escapes root
	check = candidate
	while check != root and check != check.parent:
		if check.is_symlink() and not check.resolve().is_relative_to(root):
			raise PathSecurityError("Path escapes allowed root via symlink")
		check = check.parent
	return str(candidate)

def relative_path_under_root(file_path, root_path):
	"""Return a POSIX relative path if file_path is inside root_path"""
	root = Path(root_path).expanduser().resolve()
	candidate = Path(file_path).expanduser().resolve(strict=False)
	if not candidate.is_relative_to(root):
		raise PathSecurityError("Path escapes allowed root")
	# Belt-and-suspenders: ensure no existing symlink in the path escapes root
	check = candidate
	while check != root and check != check.parent:
		if check.is_symlink() and not check.resolve().is_relative_to(root):
			raise PathSecurityError("Path escapes allowed root via symlink")
		check = check.parent
	return candidate.relative_to(root).as_posix()
