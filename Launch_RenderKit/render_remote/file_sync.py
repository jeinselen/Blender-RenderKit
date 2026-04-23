import bpy
import hashlib
import os
import re
from pathlib import Path
from .constants import PROTOCOL_MAX_FILE_SIZE
from .paths import (FileFilter, PathSecurityError, normalize_relative_path,
                    is_reserved_input_manifest_path, relative_path_under_root)

class FileSyncManager:
	"""Handles file synchronization between source and target computers"""

	def __init__(self):
		self.chunk_size = 64 * 1024  # 64KB chunks for file transfer
		self.max_file_size = PROTOCOL_MAX_FILE_SIZE

	def get_project_root(self, blend_file_path=None):
		"""Get the project root directory (parent of blend file directory)"""
		if not blend_file_path:
			blend_file_path = bpy.data.filepath

		if not blend_file_path or not os.path.isabs(blend_file_path):
			return None

		blend_dir = os.path.dirname(os.path.abspath(blend_file_path))
		project_root = os.path.dirname(blend_dir)

		return project_root

	def validate_file_scope(self, file_path, project_root):
		"""Check if file is within allowed scope (project root and subdirectories)"""
		if not project_root:
			return False

		try:
			relative_path_under_root(file_path, project_root)
			return True
		except (PathSecurityError, OSError, ValueError):
			return False

	def _normalize_filesystem_path(self, file_path):
		"""Return an absolute normalized path for Blender file references"""
		if not file_path:
			return None
		try:
			abs_path = bpy.path.abspath(file_path)
			if not abs_path:
				return None
			return str(Path(abs_path).expanduser().resolve(strict=False))
		except Exception:
			return None

	def _remember_reference(self, references, file_path, role):
		"""Add a referenced path unless it is a known system/temp file"""
		abs_path = self._normalize_filesystem_path(file_path)
		if not abs_path:
			return
		if FileFilter.should_ignore_file(abs_path, is_dependency_scan=True):
			return
		references.setdefault(abs_path, role)

	def _expand_sequence_files(self, file_path):
		"""Find existing files that belong to an image/volume/movie sequence"""
		abs_path = self._normalize_filesystem_path(file_path)
		if not abs_path:
			return []

		path = Path(abs_path)
		parent = path.parent
		name = path.name
		if not parent.exists():
			return []

		patterns = set()
		if '#' in name:
			patterns.add(re.sub(r'#+', lambda match: '[0-9]' * len(match.group(0)), name))
		if '<UDIM>' in name:
			patterns.add(name.replace('<UDIM>', '[0-9][0-9][0-9][0-9]'))
		if '<UVTILE>' in name:
			patterns.add(name.replace('<UVTILE>', 'u[0-9]*_v[0-9]*'))

		match = re.match(r'^(.*?)(\d+)(\.[^.]+)$', name)
		if match:
			prefix, digits, suffix = match.groups()
			patterns.add(f"{prefix}{'[0-9]' * len(digits)}{suffix}")

		expanded = []
		for pattern in patterns:
			for candidate in parent.glob(pattern):
				if candidate.is_file():
					expanded.append(str(candidate.resolve(strict=False)))
		return sorted(set(expanded))

	def _add_file_reference(self, references, file_path, role, include_sequences=False, include_directory=False, project_root=None):
		"""Add a single file reference, expanding sequences or referenced directories when needed"""
		abs_path = self._normalize_filesystem_path(file_path)
		if not abs_path:
			return

		if project_root and not self.validate_file_scope(abs_path, project_root):
			self._remember_reference(references, abs_path, role)
			return

		if include_directory and os.path.isdir(abs_path):
			for directory_file in self._iter_referenced_directory(abs_path):
				self._remember_reference(references, directory_file, role)
			return

		expanded_paths = self._expand_sequence_files(abs_path) if include_sequences else []
		if expanded_paths:
			for expanded_path in expanded_paths:
				self._remember_reference(references, expanded_path, role)
		else:
			self._remember_reference(references, abs_path, role)

	def _iter_referenced_directory(self, directory_path):
		"""Yield files under an explicitly referenced directory"""
		abs_directory = self._normalize_filesystem_path(directory_path)
		if not abs_directory or not os.path.isdir(abs_directory):
			return

		for root, dirs, files in os.walk(abs_directory):
			dirs[:] = [
				d for d in dirs
				if not FileFilter.should_ignore_file(os.path.join(root, d), is_dependency_scan=True)
			]
			for file_name in files:
				file_path = os.path.join(root, file_name)
				if not FileFilter.should_ignore_file(file_path, is_dependency_scan=True):
					yield file_path

	def _add_optional_filepath_attr(self, references, owner, attr_name, role, include_sequences=False, include_directory=False, project_root=None):
		"""Add a filepath-like Blender attribute when it exists and is populated"""
		try:
			file_path = getattr(owner, attr_name, None)
		except Exception:
			return
		if isinstance(file_path, str) and file_path:
			self._add_file_reference(
				references,
				file_path,
				role,
				include_sequences=include_sequences,
				include_directory=include_directory,
				project_root=project_root
			)

	def _iter_node_trees(self):
		"""Yield node trees that may contain file-backed nodes"""
		for collection_name in ('materials', 'worlds', 'scenes', 'node_groups'):
			collection = getattr(bpy.data, collection_name, ())
			for item in collection:
				node_tree = getattr(item, 'node_tree', None)
				if node_tree:
					yield node_tree

	def scan_blend_dependencies(self, blend_file_path=None):
		"""Scan blend file for all dependencies and categorize them"""
		if not blend_file_path:
			blend_file_path = bpy.data.filepath

		if not blend_file_path:
			return {'internal': [], 'external': [], 'missing': [], 'roles': {}}

		project_root = self.get_project_root(blend_file_path)
		dependencies = {'internal': [], 'external': [], 'missing': [], 'roles': {}}

		# Always include the blend file itself as an internal dependency
		if os.path.exists(blend_file_path) and self.validate_file_scope(blend_file_path, project_root):
			dependencies['internal'].append(blend_file_path)
			dependencies['roles'][blend_file_path] = 'blend'

		# Collect all file references from Blender
		file_references = {}

		# Images
		for img in bpy.data.images:
			if img.filepath and not img.packed_file:
				self._add_file_reference(
					file_references,
					img.filepath,
					'image',
					include_sequences=getattr(img, 'source', '') in {'SEQUENCE', 'TILED'},
					project_root=project_root
				)

		# Sounds
		for sound in bpy.data.sounds:
			if sound.filepath and not sound.packed_file:
				self._add_file_reference(file_references, sound.filepath, 'sound', project_root=project_root)

		# Movie clips
		for clip in bpy.data.movieclips:
			if clip.filepath:
				self._add_file_reference(
					file_references,
					clip.filepath,
					'movie_clip',
					include_sequences=getattr(clip, 'source', '') == 'SEQUENCE',
					project_root=project_root
				)

		# Fonts
		for font in bpy.data.fonts:
			if font.filepath:
				self._add_file_reference(file_references, font.filepath, 'font', project_root=project_root)

		# Libraries (linked files)
		for lib in bpy.data.libraries:
			if lib.filepath:
				self._add_file_reference(file_references, lib.filepath, 'library', project_root=project_root)

		# Cache files, volumes, and external data blocks
		for cache_file in getattr(bpy.data, 'cache_files', []):
			self._add_optional_filepath_attr(file_references, cache_file, 'filepath', 'cache_file', project_root=project_root)

		for volume in getattr(bpy.data, 'volumes', []):
			self._add_optional_filepath_attr(file_references, volume, 'filepath', 'volume', include_sequences=True, project_root=project_root)

		# Modifier and simulation cache references
		for obj in bpy.data.objects:
			self._add_optional_filepath_attr(file_references, getattr(obj, 'data', None), 'filepath', 'object_data', include_sequences=True, project_root=project_root)

			for modifier in obj.modifiers:
				self._add_optional_filepath_attr(file_references, modifier, 'filepath', 'modifier', include_sequences=True, include_directory=True, project_root=project_root)
				cache_file = getattr(modifier, 'cache_file', None)
				if cache_file:
					self._add_optional_filepath_attr(file_references, cache_file, 'filepath', 'modifier_cache_file', project_root=project_root)

				domain_settings = getattr(modifier, 'domain_settings', None)
				if domain_settings:
					self._add_optional_filepath_attr(
						file_references,
						domain_settings,
						'cache_directory',
						'simulation_cache',
						include_directory=True,
						project_root=project_root
					)

		# Check for particle cache files
		for obj in bpy.data.objects:
			for modifier in obj.modifiers:
				if modifier.type == 'PARTICLE_SYSTEM':
					psys = modifier.particle_system
					if psys.settings.type == 'HAIR':
						continue
					# Point cache files
					if hasattr(psys, 'point_cache') and psys.point_cache.filepath:
						self._add_file_reference(
							file_references,
							psys.point_cache.filepath,
							'particle_cache',
							include_directory=True,
							project_root=project_root
						)

		# File-backed nodes that are not represented by bpy.data.images
		for node_tree in self._iter_node_trees():
			for node in node_tree.nodes:
				self._add_optional_filepath_attr(file_references, node, 'filepath', 'node_file', include_sequences=True, project_root=project_root)

		# Categorize files (excluding the blend file since we already added it)
		for file_path, role in sorted(file_references.items()):
			# Skip the blend file itself since we already added it
			if file_path == blend_file_path:
				continue

			if not self.validate_file_scope(file_path, project_root):
				dependencies['external'].append(file_path)
				dependencies['roles'][file_path] = role
			elif not os.path.exists(file_path):
				dependencies['missing'].append(file_path)
				dependencies['roles'][file_path] = role
			else:
				dependencies['internal'].append(file_path)
				dependencies['roles'][file_path] = role

		return dependencies

	def calculate_file_hash(self, file_path):
		"""Calculate SHA-256 hash of a file"""
		try:
			hash_sha256 = hashlib.sha256()
			with open(file_path, "rb") as f:
				for chunk in iter(lambda: f.read(4096), b""):
					hash_sha256.update(chunk)
			return hash_sha256.hexdigest()
		except OSError:
			return None

	def get_referenced_files_manifest(self, project_root, dependencies):
		"""Create a manifest of only referenced files with hashes and metadata"""
		manifest = {}
		roles = dependencies.get('roles', {})

		try:
			print(f"Creating manifest for {len(dependencies['internal'])} internal files")

			for file_path in dependencies['internal']:
				if os.path.exists(file_path):
					try:
						rel_path = relative_path_under_root(file_path, project_root)

						# Additional filter for files that shouldn't be in manifest
						if FileFilter.should_ignore_file(file_path):
							print(f"Skipping filtered file: {file_path}")
							continue

						stat = os.stat(file_path)
						file_hash = self.calculate_file_hash(file_path)

						if file_hash:
							manifest[rel_path] = {
								'hash': file_hash,
								'size': stat.st_size,
								'mtime': stat.st_mtime,
								'role': roles.get(file_path, 'input'),
								'abs_path': file_path
							}
							print(f"Added to manifest: {rel_path} ({stat.st_size} bytes)")
						else:
							print(f"Could not hash file: {file_path}")

					except (PathSecurityError, ValueError) as e:
						print(f"Path error for {file_path}: {e}")
					except Exception as e:
						print(f"Error processing file {file_path}: {e}")
				else:
					print(f"Referenced file not found: {file_path}")

		except Exception as e:
			print(f"Error creating referenced files manifest: {e}")

		print(f"Manifest created with {len(manifest)} files")
		return manifest

	def get_directory_manifest(self, directory_path):
		"""Create a manifest of all files in directory with hashes and metadata"""
		manifest = {}

		try:
			for root, dirs, files in os.walk(directory_path):
				# Filter out ignored directories
				dirs[:] = [d for d in dirs if not FileFilter.should_ignore_file(os.path.join(root, d))]

				for file in files:
					file_path = os.path.join(root, file)

					# Skip files that should be ignored
					if FileFilter.should_ignore_file(file_path):
						continue

					try:
						rel_path = relative_path_under_root(file_path, directory_path)
					except PathSecurityError:
						continue

					try:
						stat = os.stat(file_path)
						file_hash = self.calculate_file_hash(file_path)

						if file_hash:
							manifest[rel_path] = {
								'hash': file_hash,
								'size': stat.st_size,
								'mtime': stat.st_mtime,
								'abs_path': file_path
							}
					except Exception as e:
						print(f"Error processing file {file_path}: {e}")

		except Exception as e:
			print(f"Error scanning directory {directory_path}: {e}")

		return manifest

	def sanitize_manifest_entry(self, entry):
		"""Return storage-safe manifest metadata for a synced input file"""
		if not isinstance(entry, dict):
			return {}

		safe_entry = {}
		for key in ('hash', 'size', 'mtime', 'role'):
			if key in entry:
				safe_entry[key] = entry[key]
		return safe_entry

	def sanitize_input_manifest(self, manifest):
		"""Normalize manifest keys and strip local-only filesystem paths"""
		safe_manifest = {}
		if not isinstance(manifest, dict):
			return safe_manifest

		for rel_path, entry in manifest.items():
			try:
				normalized_path = normalize_relative_path(rel_path)
			except PathSecurityError:
				continue

			if is_reserved_input_manifest_path(normalized_path):
				continue

			safe_manifest[normalized_path] = self.sanitize_manifest_entry(entry)

		return safe_manifest

	def compare_manifests(self, local_manifest, remote_manifest):
		"""Compare local input manifest with target-owned input manifest"""
		changes = {
			'new_files': [],
			'modified_files': [],
			'deleted_files': [],
			'unchanged_files': []
		}

		local_manifest = self.sanitize_input_manifest(local_manifest)
		remote_manifest = self.sanitize_input_manifest(remote_manifest)

		# Files that exist locally
		for rel_path, local_info in local_manifest.items():
			if rel_path not in remote_manifest:
				changes['new_files'].append({
					'path': rel_path,
					'size': local_info['size'],
					'local_info': local_info
				})
			elif local_info.get('hash') != remote_manifest[rel_path].get('hash'):
				changes['modified_files'].append({
					'path': rel_path,
					'size': local_info['size'],
					'local_info': local_info,
					'remote_info': remote_manifest[rel_path]
				})
			else:
				changes['unchanged_files'].append(rel_path)

		# Files that were owned by the previous target input manifest but no longer exist locally
		for rel_path in remote_manifest:
			if rel_path not in local_manifest:
				changes['deleted_files'].append({
					'path': rel_path,
					'remote_info': remote_manifest[rel_path]
				})

		return changes

# Global sync manager instance
file_sync_manager = FileSyncManager()
