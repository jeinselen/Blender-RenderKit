import atexit
import bpy
import socket
import ssl
import subprocess
import json
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
import os
import shutil
import struct
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from bpy.props import StringProperty, EnumProperty, BoolProperty, IntProperty, FloatProperty, CollectionProperty
from bpy.types import Operator, Panel, AddonPreferences, PropertyGroup
from bpy.app.handlers import persistent

ADDON_VERSION = "0.2.0"  # mirrors blender_manifest.toml version field

PROTOCOL_MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB JSON envelope limit
PROTOCOL_MAX_FILE_SIZE = 128 * 1024 * 1024 * 1024  # 128GB file transfer limit
AUTH_PBKDF2_ITERATIONS = 200_000
AUTH_CHALLENGE_TIMEOUT = 60
AUTH_TOKEN_TIMEOUT = 60 * 60
AUTH_MAX_CHALLENGES = 256
AUTH_RATE_LIMIT_WINDOW = 60   # seconds in which failures are counted
AUTH_RATE_LIMIT_MAX = 5       # max failures per window before IP is temporarily blocked
DISCOVERY_REPLY_TIMEOUT = 1.0
CLIENT_READ_TIMEOUT = 30.0
DISCOVERY_BROADCAST_TIMEOUT = 0.5
PROJECT_ID_HASH_LENGTH = 12
PROJECT_ID_SLUG_LENGTH = 60
INPUT_MANIFEST_FILENAME = ".render_remote_input_manifest.json"
INPUT_MANIFEST_VERSION = 1
OUTPUT_SYNC_POLL_INTERVAL = 2.0
OUTPUT_SYNC_QUIET_PERIOD = 6.0

LAN_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
	'10.0.0.0/8',
	'172.16.0.0/12',
	'192.168.0.0/16',
	'169.254.0.0/16',
	'127.0.0.0/8',
	'::1/128',
	'fc00::/7',
	'fe80::/10',
))

# ----
# File Filtering Utilities
# ----

class FileFilter:
	"""Centralized file filtering logic"""
	
	# Files that should never be synced (OS, temp, backup files)
	IGNORE_EXTENSIONS = {
		'.tmp', '.temp', '.log', '.lock', '.bak', '.backup',
		'.blend1', '.blend2', '.blend3',  # Blender backups
		'.ds_store', '._.ds_store',  # macOS
		'thumbs.db', 'desktop.ini',  # Windows
		'.directory',  # Linux
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
		
		# Always ignore certain extensions
		if file_ext in cls.IGNORE_EXTENSIONS or file_name in cls.IGNORE_EXTENSIONS:
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

# ----
# Timer Management System
# ----

class TimerManager:
	"""Centralized timer management to prevent registration issues"""
	
	def __init__(self):
		self.active_timers = set()
		self.timer_callbacks = {}
	
	def register_timer(self, callback, interval=1.0, persistent=False):
		"""Register a timer with proper tracking"""
		if callback in self.active_timers:
			return None # Timer was cancelled

		def wrapper():
			try:
				# Check if callback was cancelled
				if callback not in self.active_timers:
					return None  # Timer was cancelled
				
				result = callback()
				
				# Handle different return values
				if result is None or result is False:
					# Callback wants to stop
					self.unregister_timer(callback)
					return None
				elif persistent and isinstance(result, (int, float)) and result > 0:
					# Persistent timer with custom interval
					return result
				elif persistent:
					# Persistent timer with default interval
					return interval
				else:
					# One-shot timer, stop after execution
					self.unregister_timer(callback)
					return None
				
			except Exception as e:
				print(f"Timer callback error: {e}")
				self.unregister_timer(callback)
				return None
		
		self.active_timers.add(callback)
		self.timer_callbacks[callback] = wrapper
		
		try:
			bpy.app.timers.register(wrapper, first_interval=interval)
			return True
		except Exception as e:
			print(f"Failed to register timer: {e}")
			self.active_timers.discard(callback)
			if callback in self.timer_callbacks:
				del self.timer_callbacks[callback]
			return False
	
	def unregister_timer(self, callback):
		"""Unregister a specific timer"""
		if callback in self.active_timers:
			self.active_timers.discard(callback)
			if callback in self.timer_callbacks:
				wrapper = self.timer_callbacks.pop(callback)
				try:
					# Check if timer is actually registered before trying to unregister
					# Use hasattr to check if is_registered method exists (newer Blender versions)
					if hasattr(bpy.app.timers, 'is_registered'):
						if bpy.app.timers.is_registered(wrapper):
							bpy.app.timers.unregister(wrapper)
					else:
						# For older Blender versions, just try to unregister
						bpy.app.timers.unregister(wrapper)
				except (ValueError, AttributeError, RuntimeError):
					# Timer was already unregistered, doesn't exist, or Blender is shutting down
					pass
	
	def cleanup_all(self):
		"""Clean up all registered timers"""
		# Make a copy of the set to iterate over since we'll be modifying it
		active_timers_copy = self.active_timers.copy()
		for callback in active_timers_copy:
			self.unregister_timer(callback)
		
		# Clear any remaining references
		self.active_timers.clear()
		self.timer_callbacks.clear()

# Global timer manager
timer_manager = TimerManager()

# ----
# Protocol Helpers
# ----

class ProtocolError(Exception):
	"""Raised when a network message or file payload violates protocol limits"""
	pass

class PathSecurityError(Exception):
	"""Raised when a network-supplied path escapes an allowed root"""
	pass

def error_response(code, message):
	"""Build a structured error without local filesystem details"""
	return {'status': 'error', 'code': code, 'message': message}

def validate_message(msg, schema):
	"""Check that msg contains all required fields with the correct types.

	schema: {field_name: type_or_tuple_of_types}
	Returns a list of human-readable error strings; empty means valid.
	"""
	errors = []
	for field, expected in schema.items():
		if field not in msg:
			errors.append(f"missing required field '{field}'")
		elif not isinstance(msg[field], expected):
			type_desc = expected.__name__ if isinstance(expected, type) else repr(expected)
			errors.append(f"field '{field}' must be {type_desc}")
	return errors

def validate_file_size(file_size):
	"""Normalize and validate file payload sizes"""
	try:
		file_size = int(file_size)
	except (TypeError, ValueError):
		raise ProtocolError("Invalid file size")
	if file_size < 0:
		raise ProtocolError("Invalid file size")
	if file_size > PROTOCOL_MAX_FILE_SIZE:
		raise ProtocolError("File is too large")
	return file_size

def parse_ip_address(ip):
	"""Parse an IP address string, including scoped IPv6 addresses"""
	ip_string = str(ip).strip()
	if '%' in ip_string:
		ip_string = ip_string.split('%', 1)[0]
	address = ipaddress.ip_address(ip_string)
	if getattr(address, 'ipv4_mapped', None):
		return address.ipv4_mapped
	return address

def is_allowed_lan_ip(ip):
	"""Return True only for loopback, private, and link-local peers"""
	try:
		address = parse_ip_address(ip)
	except ValueError:
		return False
	return any(address in network for network in LAN_ALLOWED_NETWORKS)

def normalize_project_id(project_name):
	"""Convert a user-facing project name into a bounded cache directory id"""
	raw_name = str(project_name or "default").strip() or "default"
	digest = hashlib.sha256(raw_name.encode('utf-8')).hexdigest()[:PROJECT_ID_HASH_LENGTH]
	slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', raw_name).strip('._-')
	slug = slug[:PROJECT_ID_SLUG_LENGTH].strip('._-') or "project"
	return f"{slug}-{digest}"

def build_source_project_cache_name(project_name, blend_file_path=None):
	"""Build a stable source-side cache identity for one blend project"""
	raw_name = str(project_name or "Untitled").strip() or "Untitled"
	if not blend_file_path:
		try:
			blend_file_path = bpy.data.filepath
		except Exception:
			blend_file_path = None

	if not blend_file_path:
		raise ValueError("Saved blend file required for remote project cache identity")

	blend_path = str(Path(blend_file_path).expanduser().resolve(strict=False))
	blend_name = Path(blend_path).name or "project.blend"
	blend_hash = hashlib.sha256(blend_path.encode('utf-8')).hexdigest()[:PROJECT_ID_HASH_LENGTH]
	return f"{raw_name}-{blend_name}-{blend_hash}"

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

def recv_exact(sock, byte_count):
	"""Read exactly byte_count bytes from a socket"""
	if byte_count < 0:
		raise ProtocolError("Invalid byte count")
	chunks = []
	remaining = byte_count
	while remaining > 0:
		chunk = sock.recv(min(remaining, 64 * 1024))
		if not chunk:
			raise ProtocolError("Connection closed")
		chunks.append(chunk)
		remaining -= len(chunk)
	return b''.join(chunks)

def send_message(sock, message):
	"""Send a bounded length-prefixed JSON message"""
	try:
		message_data = json.dumps(message).encode('utf-8')
	except (TypeError, ValueError):
		raise ProtocolError("Message is not JSON serializable")
	if len(message_data) > PROTOCOL_MAX_MESSAGE_SIZE:
		raise ProtocolError("Message is too large")
	sock.sendall(struct.pack('!I', len(message_data)))
	sock.sendall(message_data)

def recv_message(sock):
	"""Receive a bounded length-prefixed JSON message"""
	length_data = recv_exact(sock, 4)
	message_length = struct.unpack('!I', length_data)[0]
	if message_length <= 0:
		raise ProtocolError("Invalid message size")
	if message_length > PROTOCOL_MAX_MESSAGE_SIZE:
		raise ProtocolError("Message is too large")
	message_data = recv_exact(sock, message_length)
	try:
		return json.loads(message_data.decode('utf-8'))
	except (UnicodeDecodeError, json.JSONDecodeError):
		raise ProtocolError("Invalid JSON message")

def send_file(sock, file_path, file_size=None):
	"""Send a bounded file payload"""
	if file_size is None:
		file_size = os.path.getsize(file_path)
	file_size = validate_file_size(file_size)
	bytes_sent = 0
	with open(file_path, 'rb') as f:
		while bytes_sent < file_size:
			chunk = f.read(min(file_sync_manager.chunk_size, file_size - bytes_sent))
			if not chunk:
				raise ProtocolError("Incomplete file read")
			sock.sendall(chunk)
			bytes_sent += len(chunk)

def recv_file(sock, target_file_path, file_size):
	"""Receive a bounded file payload into a temporary sibling path"""
	file_size = validate_file_size(file_size)
	temp_file_path = f"{target_file_path}.part"
	bytes_received = 0
	try:
		with open(temp_file_path, 'wb') as f:
			while bytes_received < file_size:
				chunk = recv_exact(sock, min(file_sync_manager.chunk_size, file_size - bytes_received))
				f.write(chunk)
				bytes_received += len(chunk)
		os.replace(temp_file_path, target_file_path)
	except Exception:
		try:
			if os.path.exists(temp_file_path):
				os.remove(temp_file_path)
		except OSError:
			pass
		raise

# ----
# File Synchronization Manager
# ----

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

# ----
# Security and Authentication
# ----

class SecureConnection:
	"""Handles authentication state for remote TCP messages"""
	
	def __init__(self):
		self._lock = threading.RLock()
		self.auth_tokens = {}
		self.auth_challenges = {}
		self.connection_timeout = 300  # 5 minutes
		self._auth_failures = {}       # ip -> [timestamp, ...]
		self._cert_path = None
		self._key_path = None
		self._tls_ready = False

	# ---- TLS certificate management ----

	def prepare_tls(self, cert_dir):
		"""Generate or load the node TLS cert. Must be called from the main thread before starting servers."""
		cert_path = os.path.join(cert_dir, 'server.crt')
		key_path = os.path.join(cert_dir, 'server.key')
		if not (os.path.exists(cert_path) and os.path.exists(key_path)):
			try:
				subprocess.run(
					['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
					 '-keyout', key_path, '-out', cert_path,
					 '-days', '3650', '-nodes', '-subj', '/CN=render-remote'],
					check=True, capture_output=True
				)
				if os.name != 'nt':
					os.chmod(key_path, 0o600)
					os.chmod(cert_path, 0o600)
			except (subprocess.CalledProcessError, FileNotFoundError) as e:
				raise RuntimeError(
					f"Cannot generate TLS certificate — ensure openssl is installed and on PATH: {e}"
				)
		with self._lock:
			self._cert_path = cert_path
			self._key_path = key_path
			self._tls_ready = True

	def get_cert_fingerprint(self):
		"""Return the SHA-256 hex fingerprint of this node's TLS certificate."""
		with self._lock:
			cert_path = self._cert_path
		if not cert_path or not os.path.exists(cert_path):
			return None
		try:
			import base64
			with open(cert_path, 'rb') as f:
				pem_data = f.read().decode('ascii', errors='replace')
			lines = pem_data.strip().splitlines()
			der_b64 = ''.join(l for l in lines if not l.startswith('-----'))
			der_bytes = base64.b64decode(der_b64)
			return hashlib.sha256(der_bytes).hexdigest()
		except Exception:
			return None

	def regenerate_cert(self):
		"""Delete and regenerate the TLS certificate. Call from main thread only."""
		with self._lock:
			cert_path, key_path = self._cert_path, self._key_path
			self._tls_ready = False
		for path in (cert_path, key_path):
			if path and os.path.exists(path):
				try:
					os.remove(path)
				except OSError:
					pass
		if cert_path:
			self.prepare_tls(os.path.dirname(cert_path))

	def server_ssl_context(self):
		"""Return a TLS server context loaded with this node's certificate."""
		with self._lock:
			ready, cert_path, key_path = self._tls_ready, self._cert_path, self._key_path
		if not ready:
			raise RuntimeError("TLS is not ready — call prepare_tls() from the main thread first")
		ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
		ctx.load_cert_chain(cert_path, key_path)
		return ctx

	def client_ssl_context(self):
		"""Return a TLS client context that skips CA validation (fingerprint verified separately)."""
		ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
		ctx.check_hostname = False
		ctx.verify_mode = ssl.CERT_NONE  # fingerprint-pinning done post-handshake; see verify_peer_fingerprint
		return ctx

	def _fingerprint_store_path(self):
		with self._lock:
			cert_path = self._cert_path
		if cert_path:
			return os.path.join(os.path.dirname(cert_path), 'known_nodes.json')
		return None

	def verify_peer_fingerprint(self, ssl_sock, node_id):
		"""TOFU fingerprint check: auto-pin first connection; reject mismatches."""
		peer_cert_der = ssl_sock.getpeercert(binary_form=True)
		if peer_cert_der is None:
			raise ProtocolError("Peer provided no TLS certificate")
		fingerprint = hashlib.sha256(peer_cert_der).hexdigest()

		store_path = self._fingerprint_store_path()
		if store_path is None:
			return fingerprint  # TLS not fully configured; skip pinning

		try:
			with open(store_path, 'r', encoding='utf-8') as f:
				store = json.load(f)
		except (FileNotFoundError, json.JSONDecodeError):
			store = {}

		pinned = store.get(node_id)
		if pinned is None:
			store[node_id] = fingerprint
			try:
				with open(store_path, 'w', encoding='utf-8') as f:
					json.dump(store, f, indent=2)
			except OSError as e:
				print(f"Render Remote: Could not save TLS fingerprint pin: {e}")
			print(f"Render Remote: TOFU pinned fingerprint for {node_id}: {fingerprint[:16]}...")
		elif not hmac.compare_digest(pinned, fingerprint):
			raise ProtocolError(
				f"TLS fingerprint mismatch for {node_id} — connection refused. "
				f"If the remote certificate changed legitimately, clear the pin in preferences."
			)
		return fingerprint

	# ---- Rate-limit helpers ----

	def _record_auth_failure(self, ip):
		now = time.time()
		with self._lock:
			bucket = self._auth_failures.setdefault(ip, [])
			bucket.append(now)
			self._auth_failures[ip] = [t for t in bucket if now - t < AUTH_RATE_LIMIT_WINDOW]

	def _is_auth_blocked(self, ip):
		now = time.time()
		with self._lock:
			recent = [t for t in self._auth_failures.get(ip, []) if now - t < AUTH_RATE_LIMIT_WINDOW]
			return len(recent) >= AUTH_RATE_LIMIT_MAX

	# ---- Token generation ----

	def generate_auth_token(self):
		"""Generate a secure authentication token"""
		return secrets.token_urlsafe(32)
	
	def hash_password(self, password, salt=None):
		"""Hash password with salt for secure storage"""
		if salt is None:
			salt = secrets.token_hex(16)
		return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), AUTH_PBKDF2_ITERATIONS), salt

	def build_auth_proof(self, password_hash, client_nonce, server_nonce):
		"""Build a challenge response proof without sending the passcode"""
		message = f"{client_nonce}:{server_nonce}".encode()
		return hmac.new(password_hash, message, hashlib.sha256).hexdigest()

	def create_challenge(self, ip, salt):
		"""Create a short-lived authentication challenge"""
		with self._lock:
			self.cleanup_expired_auth()
			if len(self.auth_challenges) >= AUTH_MAX_CHALLENGES:
				oldest_nonce = min(
					self.auth_challenges,
					key=lambda nonce: self.auth_challenges[nonce].get('created', 0)
				)
				self.auth_challenges.pop(oldest_nonce, None)

			client_nonce = secrets.token_urlsafe(24)
			server_nonce = secrets.token_urlsafe(24)
			self.auth_challenges[client_nonce] = {
				'server_nonce': server_nonce,
				'created': time.time(),
				'ip': ip
			}
			return {
				'client_nonce': client_nonce,
				'server_nonce': server_nonce,
				'salt': salt,
				'iterations': AUTH_PBKDF2_ITERATIONS,
				'algorithm': 'pbkdf2_sha256_hmac_sha256'
			}

	def consume_challenge(self, client_nonce, server_nonce, ip):
		"""Fetch and remove a valid challenge"""
		with self._lock:
			challenge = self.auth_challenges.pop(client_nonce, None)
			if not challenge:
				return None
			if challenge.get('server_nonce') != server_nonce:
				return None
			if challenge.get('ip') != ip:
				return None
			if time.time() - challenge.get('created', 0) > AUTH_CHALLENGE_TIMEOUT:
				return None
			return challenge

	def issue_auth_token(self, ip):
		"""Issue an expiring token bound to the peer IP"""
		with self._lock:
			auth_token = self.generate_auth_token()
			now = time.time()
			self.auth_tokens[auth_token] = {
				'created': now,
				'expires': now + AUTH_TOKEN_TIMEOUT,
				'ip': ip
			}
			return auth_token

	def verify_auth_token(self, auth_token, ip):
		"""Verify an auth token and remove stale tokens"""
		with self._lock:
			self.cleanup_expired_auth()
			token_info = self.auth_tokens.get(auth_token)
			if not token_info:
				return False
			if not hmac.compare_digest(token_info.get('ip', ''), ip):
				return False
			if token_info.get('expires', 0) < time.time():
				self.auth_tokens.pop(auth_token, None)
				return False
			return True

	def cleanup_expired_auth(self):
		"""Remove expired auth tokens and challenges"""
		with self._lock:
			now = time.time()
			self.auth_tokens = {
				token: info for token, info in self.auth_tokens.items()
				if info.get('expires', 0) >= now
			}
			self.auth_challenges = {
				nonce: info for nonce, info in self.auth_challenges.items()
				if now - info.get('created', 0) <= AUTH_CHALLENGE_TIMEOUT
			}

	def clear_authentication(self):
		"""Clear all token and challenge state"""
		with self._lock:
			self.auth_tokens.clear()
			self.auth_challenges.clear()

# ----
# Output File Monitor (Simplified)
# ----

class OutputFileMonitor:
	"""Monitors target-side render outputs and exposes them as a manifest"""

	def __init__(self, project_root, source_project_root):
		self.project_root = str(Path(project_root).expanduser().resolve(strict=False))
		self.source_project_root = None
		if source_project_root:
			self.source_project_root = str(Path(source_project_root).expanduser().resolve(strict=False))
		self.monitoring = False
		self.monitor_thread = None
		self._stop_event = threading.Event()
		self.scan_lock = threading.Lock()
		self.manifest_lock = threading.Lock()
		self.output_roots = set()
		self.known_files = {}
		self.output_manifest = {}
		self.last_scan_time = 0.0
		self.last_output_change = time.time()

		self._configure_scene_output_paths(bpy.context.scene)
		self._scan_initial_files()

	def _make_safe_segment(self, value, fallback="output"):
		"""Create a filesystem-safe path segment"""
		text = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value or '')).strip('._-')
		return text[:64] or fallback

	def _normalize_existing_path(self, file_path):
		"""Normalize an existing output path string when possible"""
		if not file_path:
			return None
		try:
			return str(Path(file_path).expanduser().resolve(strict=False))
		except Exception:
			return None

	def _is_within_workspace(self, file_path):
		"""Return True when a path stays inside the target workspace"""
		return file_sync_manager.validate_file_scope(file_path, self.project_root)

	def _get_output_root_from_path(self, file_path):
		"""Resolve the directory root for a render output path"""
		normalized_path = self._normalize_existing_path(file_path)
		if not normalized_path:
			return None

		basename = os.path.basename(normalized_path.rstrip('/\\'))
		if not basename:
			return normalized_path

		if os.path.splitext(basename)[1]:
			return os.path.dirname(normalized_path)

		if basename.lower() in FileFilter.RENDER_OUTPUT_PATTERNS:
			return normalized_path

		if basename.endswith(('_', '-')) or '#' in basename:
			return os.path.dirname(normalized_path)

		return normalized_path

	def _resolve_output_path_under_workspace(self, path_text, fallback_relative):
		"""Map an output path to a safe location under the target workspace"""
		normalized_fallback = normalize_relative_path(fallback_relative)
		fallback_path = resolve_under_root(self.project_root, normalized_fallback)
		normalized_path = self._normalize_existing_path(path_text)
		if not normalized_path:
			return fallback_path

		if self._is_within_workspace(normalized_path):
			return normalized_path

		if self.source_project_root:
			try:
				relative_path = relative_path_under_root(normalized_path, self.source_project_root)
				return resolve_under_root(self.project_root, relative_path)
			except PathSecurityError:
				pass

		return fallback_path

	def _iter_output_file_nodes(self, scene):
		"""Yield compositor file output nodes when compositing is enabled"""
		compositing = getattr(scene, 'compositing_node_group', None) or getattr(scene, 'node_tree', None)
		if not scene.render.use_compositing or not compositing:
			return

		for node in compositing.nodes:
			if node.type == 'OUTPUT_FILE':
				yield node

	def _configure_scene_output_paths(self, scene):
		"""Rewrite output destinations to stay inside the target workspace and record roots"""
		output_roots = set()

		if scene.render.filepath:
			main_output_path = self._resolve_output_path_under_workspace(
				bpy.path.abspath(scene.render.filepath),
				"renders"
			)
			scene.render.filepath = main_output_path
			output_root = self._get_output_root_from_path(main_output_path)
			if output_root:
				output_roots.add(output_root)

		for index, node in enumerate(self._iter_output_file_nodes(scene) or []):
			path_attr = 'directory' if hasattr(node, 'directory') else 'base_path'
			configured_directory = getattr(node, path_attr, '')
			fallback_relative = f"renders/compositor/{self._make_safe_segment(node.name, f'node-{index + 1}')}"
			target_directory = self._resolve_output_path_under_workspace(
				bpy.path.abspath(configured_directory) if configured_directory else '',
				fallback_relative
			)
			setattr(node, path_attr, target_directory)
			output_roots.add(target_directory)

		self.output_roots = {
			str(Path(root).expanduser().resolve(strict=False))
			for root in output_roots
			if root and self._is_within_workspace(root)
		}
		print(f"Configured output roots: {sorted(self.output_roots)}")

	def _iter_output_files(self):
		"""Yield current files from known output roots"""
		seen_paths = set()
		for output_root in sorted(self.output_roots):
			if not os.path.exists(output_root):
				continue

			for root, dirs, files in os.walk(output_root):
				dirs[:] = [d for d in dirs if not FileFilter.should_ignore_file(os.path.join(root, d))]
				for file_name in files:
					file_path = os.path.join(root, file_name)
					if file_path in seen_paths or FileFilter.should_ignore_file(file_path):
						continue
					if not self._is_within_workspace(file_path):
						continue
					seen_paths.add(file_path)
					yield file_path

	def _infer_frame_number(self, relative_path):
		"""Best-effort frame number extraction from a rendered file path"""
		match = re.search(r'(\d{3,8})(?=\.[^.]+$)', relative_path)
		if not match:
			return None
		try:
			return int(match.group(1))
		except ValueError:
			return None

	def _update_manifest_entry(self, file_path, frame_number=None):
		"""Record a rendered output file in the output manifest"""
		normalized_path = self._normalize_existing_path(file_path)
		if not normalized_path or not os.path.isfile(normalized_path):
			return
		if not self._is_within_workspace(normalized_path):
			return

		try:
			relative_path = relative_path_under_root(normalized_path, self.project_root)
			stat = os.stat(normalized_path)
		except (OSError, PathSecurityError):
			return

		previous_state = self.known_files.get(normalized_path)
		if previous_state and previous_state['size'] == stat.st_size and previous_state['mtime'] == stat.st_mtime:
			return

		file_hash = file_sync_manager.calculate_file_hash(normalized_path)
		if not file_hash:
			return

		now = time.time()
		with self.manifest_lock:
			existing_entry = self.output_manifest.get(relative_path, {})
			stable_timestamp = existing_entry.get('timestamp', now)
			if existing_entry.get('hash') != file_hash or existing_entry.get('size') != stat.st_size:
				stable_timestamp = now

			entry = {
				'relative_path': relative_path,
				'size': stat.st_size,
				'hash': file_hash,
				'timestamp': stable_timestamp,
			}

			resolved_frame = frame_number if frame_number is not None else self._infer_frame_number(relative_path)
			if resolved_frame is not None:
				entry['frame'] = int(resolved_frame)
			elif 'frame' in existing_entry:
				entry['frame'] = existing_entry['frame']

			if entry != existing_entry:
				self.output_manifest[relative_path] = entry
				self.last_output_change = now

		self.known_files[normalized_path] = {
			'size': stat.st_size,
			'mtime': stat.st_mtime,
			'hash': file_hash,
		}

	def _remove_deleted_outputs(self, current_paths):
		"""Drop manifest entries for outputs that no longer exist on disk"""
		current_paths = {self._normalize_existing_path(path) for path in current_paths}
		for known_path in list(self.known_files):
			if known_path in current_paths:
				continue
			if not self._is_within_workspace(known_path):
				self.known_files.pop(known_path, None)
				continue

			try:
				relative_path = relative_path_under_root(known_path, self.project_root)
			except PathSecurityError:
				self.known_files.pop(known_path, None)
				continue

			self.known_files.pop(known_path, None)
			with self.manifest_lock:
				if relative_path in self.output_manifest:
					self.output_manifest.pop(relative_path, None)
					self.last_output_change = time.time()

	def _scan_initial_files(self):
		"""Record pre-existing output files so only new/changed render outputs are tracked"""
		file_count = 0
		for file_path in self._iter_output_files():
			try:
				stat = os.stat(file_path)
				self.known_files[file_path] = {
					'size': stat.st_size,
					'mtime': stat.st_mtime,
					'hash': None,
				}
				file_count += 1
			except OSError:
				continue

		print(f"Initial output scan complete: {file_count} existing files recorded")

	def start_monitoring(self):
		"""Start background monitoring for render outputs"""
		if self.monitoring:
			return

		self._stop_event.clear()
		self.monitoring = True
		print(f"Started monitoring output roots: {sorted(self.output_roots)}")

		if not self.monitor_thread or not self.monitor_thread.is_alive():
			self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
			self.monitor_thread.start()

	def stop_monitoring(self):
		"""Stop monitoring and perform a final output scan"""
		if not self.monitoring:
			return

		print("Stopping output file monitoring...")
		self.monitoring = False
		self._stop_event.set()

		if self.monitor_thread:
			self.monitor_thread.join(timeout=5)

		self._final_sync_scan()

	def _monitor_loop(self):
		"""Monitor outputs for new and changed files while rendering continues"""
		while self.monitoring:
			try:
				now = time.time()
				if now - self.last_scan_time >= OUTPUT_SYNC_POLL_INTERVAL:
					self._scan_for_new_files()
					self.last_scan_time = now
				self._stop_event.wait(timeout=1.0)
			except Exception as e:
				print(f"Output monitor loop error: {e}")
				self._stop_event.wait(timeout=2.0)

		print("Output monitoring loop stopped")

	def _get_expected_frame_outputs(self, scene):
		"""Get expected output file paths for the current frame"""
		outputs = []

		if scene.render.filepath:
			base_path = bpy.path.abspath(scene.render.filepath)
			if scene.render.use_file_extension:
				if scene.frame_current != scene.frame_start or (scene.frame_end - scene.frame_start) > 0:
					path_parts = os.path.splitext(base_path)
					frame_str = f"{scene.frame_current:04d}"

					file_format = scene.render.image_settings.file_format
					if file_format == 'PNG':
						ext = '.png'
					elif file_format == 'JPEG':
						ext = '.jpg'
					elif file_format == 'OPEN_EXR':
						ext = '.exr'
					elif file_format == 'TIFF':
						ext = '.tif'
					else:
						ext = path_parts[1] if path_parts[1] else '.png'

					main_output = f"{path_parts[0]}{frame_str}{ext}"
				else:
					main_output = base_path
			else:
				main_output = base_path

			outputs.append(main_output)

		for node in self._iter_output_file_nodes(scene) or []:
			directory = getattr(node, 'directory', None) or getattr(node, 'base_path', None)
			if not directory:
				continue
			directory = bpy.path.abspath(directory)
			for input_socket in node.inputs:
				if not input_socket.is_linked:
					continue

				if input_socket.name != 'Image':
					filename = f"{input_socket.name}{scene.frame_current:04d}"
				else:
					filename = f"Image{scene.frame_current:04d}"

				if hasattr(node, 'format'):
					if node.format.file_format == 'PNG':
						filename += '.png'
					elif node.format.file_format == 'JPEG':
						filename += '.jpg'
					elif node.format.file_format == 'OPEN_EXR':
						filename += '.exr'
					else:
						filename += '.png'
				else:
					filename += '.png'

				outputs.append(os.path.join(directory, filename))

		return outputs

	def _scan_for_new_files(self):
		"""Scan monitored output roots and update the output manifest"""
		with self.scan_lock:
			current_paths = []
			for file_path in self._iter_output_files():
				current_paths.append(file_path)
				self._update_manifest_entry(file_path)

			self._remove_deleted_outputs(current_paths)

	def get_output_manifest(self):
		"""Return a copy of the current output manifest"""
		with self.manifest_lock:
			return {
				relative_path: entry.copy()
				for relative_path, entry in sorted(self.output_manifest.items())
			}

	def get_pending_files(self):
		"""Backward-compatible view of outputs as manifest entries"""
		return list(self.get_output_manifest().values())

	def remove_pending_file(self, file_path):
		"""Legacy no-op retained for older call sites"""
		return None

	def on_frame_written(self, scene, depsgraph=None):
		"""Attempt immediate capture of freshly written frame outputs"""
		if not self.monitoring:
			return

		def detect_frame_files():
			time.sleep(1.0)
			for expected_path in self._get_expected_frame_outputs(scene):
				self._update_manifest_entry(expected_path, frame_number=scene.frame_current)

		threading.Thread(target=detect_frame_files, daemon=True).start()

	def on_render_complete(self, scene, depsgraph=None):
		"""Continue scanning after render completion for post-processing outputs"""
		if not self.monitoring:
			return

		print("Render complete - monitoring post-processing outputs")
		self._scan_for_new_files()

		def post_processing_monitor():
			monitor_time = 0
			while monitor_time < 30:
				self._stop_event.wait(timeout=3)
				monitor_time += 3
				if self._stop_event.is_set():
					break
				try:
					self._scan_for_new_files()
				except Exception as e:
					print(f"Post-processing output monitor error: {e}")

			print("Post-processing monitoring completed")

		threading.Thread(target=post_processing_monitor, daemon=True).start()

	def _final_sync_scan(self):
		"""Do one last output scan before shutdown"""
		print("Performing final output scan...")
		try:
			self._stop_event.wait(timeout=2)
			self._scan_for_new_files()
			with self.manifest_lock:
				output_count = len(self.output_manifest)
			print(f"Final output scan completed. {output_count} manifest entries available.")
		except Exception as e:
			print(f"Error in final output scan: {e}")

# ----
# Network Discovery and Communication (Simplified)
# ----

class NetworkManager:
	"""Manages network discovery and communication"""
	
	def __init__(self):
		self.discovery_port = 5001
		self.communication_port = 5002
		self.broadcast_interval = 5
		self.discovery_active = False
		self.communication_active = False
		self.discovered_nodes = {}
		self.discovery_thread = None
		self.communication_thread = None
		self.security = SecureConnection()
		self.stored_password_hash = None
		self.stored_salt = None
		self._shutdown_requested = False
		self._rendering_event = threading.Event()
		self._cached_cache_root = None

	@property
	def is_rendering(self):
		return self._rendering_event.is_set()

	@is_rendering.setter
	def is_rendering(self, value):
		if value:
			self._rendering_event.set()
		else:
			self._rendering_event.clear()

	def update_ports_from_preferences(self):
		"""Snapshot preference values that handler threads need. Call from main thread only."""
		try:
			prefs = bpy.context.preferences.addons[__package__].preferences
			self.discovery_port = prefs.remote_discovery_port
			self.communication_port = prefs.remote_communication_port
			self._cached_cache_root = str(
				Path(bpy.path.abspath(prefs.remote_cache_directory)).expanduser().resolve()
			)
		except (AttributeError, KeyError):
			pass

	def _is_allowed_peer(self, ip):
		"""Allow only LAN-local peers for Render Remote sockets"""
		return is_allowed_lan_ip(ip)

	def _get_cache_root(self):
		"""Return the cached remote cache root; must be populated by update_ports_from_preferences on the main thread before handler threads call this."""
		if self._cached_cache_root is not None:
			return self._cached_cache_root
		try:
			prefs = bpy.context.preferences.addons[__package__].preferences
			return str(Path(bpy.path.abspath(prefs.remote_cache_directory)).expanduser().resolve())
		except (AttributeError, KeyError):
			raise RuntimeError("Remote cache root is not configured")

	def _get_project_cache_dir(self, project_name):
		"""Resolve a normalized project cache directory under the cache root"""
		project_id = normalize_project_id(project_name)
		return resolve_under_root(self._get_cache_root(), project_id), project_id

	def _get_input_manifest_path(self, project_cache_dir):
		"""Resolve the target-owned input manifest path for a project cache"""
		return resolve_under_root(project_cache_dir, INPUT_MANIFEST_FILENAME)

	def _load_input_manifest(self, project_cache_dir):
		"""Load the stored target-owned input manifest"""
		manifest_path = self._get_input_manifest_path(project_cache_dir)
		if not os.path.isfile(manifest_path):
			return {}

		with open(manifest_path, 'r', encoding='utf-8') as manifest_file:
			manifest_data = json.load(manifest_file)

		if isinstance(manifest_data, dict) and isinstance(manifest_data.get('files'), dict):
			return file_sync_manager.sanitize_input_manifest(manifest_data['files'])

		if isinstance(manifest_data, dict):
			return file_sync_manager.sanitize_input_manifest(manifest_data)

		return {}

	def _write_input_manifest(self, project_cache_dir, manifest):
		"""Persist the target-owned input manifest atomically"""
		os.makedirs(project_cache_dir, exist_ok=True)
		manifest_path = self._get_input_manifest_path(project_cache_dir)
		temp_path = resolve_under_root(project_cache_dir, f"{INPUT_MANIFEST_FILENAME}.tmp")
		manifest_data = {
			'version': INPUT_MANIFEST_VERSION,
			'updated_at': time.time(),
			'files': file_sync_manager.sanitize_input_manifest(manifest)
		}

		with open(temp_path, 'w', encoding='utf-8') as manifest_file:
			json.dump(manifest_data, manifest_file, indent=2, sort_keys=True)

		os.replace(temp_path, manifest_path)

	def _remove_empty_parent_dirs(self, project_cache_dir, file_path):
		"""Remove empty directories up to, but not including, the project cache root"""
		root = Path(project_cache_dir).resolve()
		current = Path(file_path).resolve(strict=False).parent

		while current != root:
			try:
				if os.path.commonpath([str(root), str(current)]) != str(root):
					break
				current.rmdir()
				current = current.parent
			except OSError:
				break

	def _create_connection(self, ip, port, timeout=10):
		"""Create an outbound connection only to an allowed LAN target"""
		if not bpy.app.online_access:
			raise ProtocolError("Network access is disabled in Blender preferences")
		if not self._is_allowed_peer(ip):
			raise ProtocolError("Remote address is not LAN-local")
		port = int(port)
		if port < 1 or port > 65535:
			raise ProtocolError("Invalid remote port")
		sock = socket.create_connection((ip, port), timeout=timeout)
		try:
			ssl_ctx = self.security.client_ssl_context()
			ssl_sock = ssl_ctx.wrap_socket(sock, server_hostname=None)
			self.security.verify_peer_fingerprint(ssl_sock, f"{ip}:{port}")
			return ssl_sock
		except (ssl.SSLError, ProtocolError):
			try:
				sock.close()
			except OSError:
				pass
			raise
		except Exception as e:
			try:
				sock.close()
			except OSError:
				pass
			raise ProtocolError(f"TLS connection to {ip}:{port} failed: {e}")
	
	def start_discovery_server(self, node_name, passcode=""):
		"""Start discovery server to announce this node"""
		if not bpy.app.online_access:
			print("Render Remote: network access is disabled in Blender preferences — cannot start discovery server")
			return False

		if self.discovery_active:
			return True

		if not self.configure_authentication(passcode):
			print("Remote render target service requires an authentication passcode")
			return False
			
		self._shutdown_requested = False
		self.discovery_active = True
		self.discovery_thread = threading.Thread(
			target=self._discovery_server_loop, 
			args=(node_name, bool(passcode)),
			daemon=True
		)
		self.discovery_thread.start()
		
		# Also start communication server
		self.start_communication_server()
		
		print(f"Discovery server started for node: {node_name}")
		return True

	def configure_authentication(self, passcode):
		"""Set the target passcode hash and revoke existing auth state"""
		self.security.clear_authentication()
		if not passcode:
			self.stored_password_hash = None
			self.stored_salt = None
			return False
		self.stored_password_hash, self.stored_salt = self.security.hash_password(passcode)
		return True

	def revoke_auth_sessions(self):
		"""Revoke active tokens and challenges while keeping the configured passcode"""
		self.security.clear_authentication()

	def clear_authentication(self):
		"""Clear active authentication state for stopped services"""
		self.security.clear_authentication()
		self.stored_password_hash = None
		self.stored_salt = None

	def stop_discovery_server(self, force=False):
		"""Stop discovery server"""
		# Don't stop if we're actively rendering
		if self.is_rendering and not force:
			print("Skipping discovery server stop - rendering in progress")
			return
			
		self._shutdown_requested = True
		self.discovery_active = False
		
		if self.discovery_thread and self.discovery_thread.is_alive():
			self.discovery_thread.join(timeout=2)
			
		self.stop_communication_server(force=force)
		self.clear_authentication()
		print("Discovery server stopped")
	
	def start_communication_server(self):
		"""Start communication server for handling connections"""
		if not bpy.app.online_access:
			print("Render Remote: network access is disabled in Blender preferences — cannot start communication server")
			return

		if self.communication_active:
			print("Communication server already active")
			return

		self.update_ports_from_preferences()

		# Set up TLS certificate on the main thread before spawning the server daemon
		try:
			cert_dir = bpy.utils.user_resource('CONFIG', path='render_remote')
			os.makedirs(cert_dir, exist_ok=True)
			self.security.prepare_tls(cert_dir)
		except Exception as e:
			print(f"Render Remote: TLS setup failed — cannot start communication server: {e}")
			self.communication_active = False
			return

		print(f"Starting communication server on port {self.communication_port}...")
		self.communication_active = True
		self.communication_thread = threading.Thread(
			target=self._communication_server_loop,
			daemon=True
		)
		self.communication_thread.start()
		
		# Give the server a moment to start
		time.sleep(0.5)
		print(f"Communication server thread started")
	
	def stop_communication_server(self, force=False):
		"""Stop communication server"""
		# Don't stop if we're actively rendering
		if self.is_rendering and not force:
			print("Skipping communication server stop - rendering in progress")
			return
			
		self.communication_active = False
		if self.communication_thread and self.communication_thread.is_alive():
			self.communication_thread.join(timeout=2)
		print("Communication server stopped")

	def shutdown(self, force=False):
		"""Stop all network activity and clear network-owned state"""
		if self.is_rendering and not force:
			print("Skipping network shutdown - rendering in progress")
			return

		self._shutdown_requested = True
		self.discovery_active = False
		self.communication_active = False

		if self.discovery_thread and self.discovery_thread.is_alive():
			self.discovery_thread.join(timeout=2)
		if self.communication_thread and self.communication_thread.is_alive():
			self.communication_thread.join(timeout=2)

		self.discovery_thread = None
		self.communication_thread = None
		self.discovered_nodes.clear()
		self.clear_authentication()
		self.is_rendering = False
		print("Network manager shut down")
	
	def _discovery_server_loop(self, node_name, requires_auth):
		"""Discovery server main loop"""
		sock = None
		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			sock.bind(('', self.discovery_port))
			sock.settimeout(DISCOVERY_REPLY_TIMEOUT)

			while self.discovery_active and not self._shutdown_requested:
				try:
					data, addr = sock.recvfrom(1024)
					if not self._is_allowed_peer(addr[0]):
						continue

					message = json.loads(data.decode())

					if message.get('type') == 'discovery_request':
						# Respond with node information
						response = {
							'type': 'discovery_response',
							'node_name': node_name,
							'ip': self._get_local_ip(),
							'port': self.communication_port,
							'blender_version': bpy.app.version_string,
							'plugin_version': ADDON_VERSION,
							'requires_auth': requires_auth,
							'fingerprint': self.security.get_cert_fingerprint(),
							'timestamp': time.time()
						}

						response_data = json.dumps(response).encode()
						sock.sendto(response_data, addr)

				except socket.timeout:
					continue
				except Exception as e:
					if self.discovery_active:  # Only log if we should be active
						print(f"Discovery server error: {e}")
					
		except Exception as e:
			print(f"Failed to start discovery server: {e}")
		finally:
			if sock:
				try:
					sock.close()
				except OSError:
					pass

	def _communication_server_loop(self):
		"""Communication server main loop for handling connections"""
		server_sock = None
		try:
			server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			
			# Try to bind to all interfaces (0.0.0.0) first, fallback to localhost
			bind_addresses = [('0.0.0.0', self.communication_port), ('', self.communication_port)]
			
			bound = False
			for bind_addr in bind_addresses:
				try:
					server_sock.bind(bind_addr)
					bound = True
					print(f"Communication server bound to {bind_addr[0] or 'localhost'}:{self.communication_port}")
					break
				except OSError as e:
					print(f"Failed to bind to {bind_addr}: {e}")
					continue
			
			if not bound:
				print(f"Failed to bind communication server to any address on port {self.communication_port}")
				return
			
			server_sock.listen(10)  # Increased queue size for multiple file transfers
			server_sock.settimeout(DISCOVERY_REPLY_TIMEOUT)
			
			print(f"Communication server listening, ready to accept connections")
			
			while self.communication_active and not self._shutdown_requested:
				try:
					client_sock, addr = server_sock.accept()
					if not self._is_allowed_peer(addr[0]):
						print(f"Rejected non-LAN connection from {addr[0]}")
						try:
							client_sock.close()
						except OSError:
							pass
						continue

					# Upgrade to TLS before handing off to handler thread
					try:
						ssl_ctx = self.security.server_ssl_context()
						client_sock = ssl_ctx.wrap_socket(client_sock, server_side=True)
					except ssl.SSLError as e:
						print(f"TLS handshake failed from {addr[0]}: {e}")
						try:
							client_sock.close()
						except OSError:
							pass
						continue

					print(f"Accepted TLS connection from {addr[0]}:{addr[1]}")

					# Handle client in separate thread
					client_thread = threading.Thread(
						target=self._handle_client,
						args=(client_sock, addr),
						daemon=True
					)
					client_thread.start()

				except socket.timeout:
					continue
				except Exception as e:
					if self.communication_active:
						print(f"Communication server error: {e}")
					
		except Exception as e:
			print(f"Failed to start communication server: {e}")
		finally:
			if server_sock:
				try:
					server_sock.close()
				except OSError:
					pass
			print("Communication server stopped")
	
	def _handle_client(self, client_sock, addr):
		"""Handle individual client connections"""
		try:
			if not self._is_allowed_peer(addr[0]):
				print(f"Rejected non-LAN client from {addr[0]}")
				return

			client_sock.settimeout(CLIENT_READ_TIMEOUT)
			while not self._shutdown_requested:
				try:
					message = recv_message(client_sock)
					msg_type = message.get('type', 'unknown')
					print(f"Received message type: {msg_type} from {addr[0]}")
					
					response = self._process_message(message, addr, client_sock)
					
					# Send response if file transfers have not already handled it.
					if response is not None:
						send_message(client_sock, response)
						print(f"Sent response: {response.get('status', 'unknown')} for {msg_type}")

				except ProtocolError as e:
					print(f"Protocol error from {addr[0]}: {e}")
					try:
						send_message(client_sock, error_response('protocol_error', str(e)))
					except Exception:
						pass
					break
				except Exception as e:
					print(f"Client handler error from {addr[0]}: {e}")
					try:
						send_message(client_sock, error_response('server_error', 'Request failed'))
					except Exception:
						pass
					break
		
		except Exception as e:
			print(f"Client connection error from {addr[0]}: {e}")
		finally:
			try:
				client_sock.close()
			except OSError:
				pass

	def _process_message(self, message, addr, client_sock):
		"""Process incoming messages from clients"""
		msg_type = message.get('type')
		
		if msg_type == 'auth_challenge':
			return self._handle_auth_challenge(message, addr)
			
		elif msg_type == 'authenticate':
			return self._handle_authenticate(message, addr)
			
		auth_error = self._require_authenticated(message, addr)
		if auth_error:
			return auth_error
			
		if msg_type == 'connection_test':
			return {'status': 'success', 'message': 'Connection successful'}
			
		elif msg_type == 'get_project_manifest':
			return self._handle_get_manifest(message)
			
		elif msg_type == 'sync_file':
			return self._handle_sync_file(message, client_sock)

		elif msg_type == 'delete_obsolete_inputs':
			return self._handle_delete_obsolete_inputs(message)
			
		elif msg_type == 'render_request':
			return self._handle_render_request(message, addr)
			
		elif msg_type == 'render_status':
			return self._handle_render_status_request(message)
			
		elif msg_type == 'render_cancel':
			return self._handle_render_cancel(message)
			
		elif msg_type == 'get_pending_files':
			return self._handle_get_pending_files(message)

		elif msg_type == 'get_output_manifest':
			return self._handle_get_output_manifest(message)
			
		elif msg_type == 'request_file':
			return self._handle_request_file(message, client_sock)
		
		else:
			return {'status': 'error', 'message': 'Unknown message type'}

	def _handle_auth_challenge(self, message, addr):
		"""Create a challenge for passcode proof authentication"""
		if self.security._is_auth_blocked(addr[0]):
			return error_response('auth_blocked', 'Too many failed authentication attempts; try again later')

		if not self.stored_password_hash or not self.stored_salt:
			return error_response('auth_not_configured', 'Authentication is not configured')

		challenge = self.security.create_challenge(addr[0], self.stored_salt)
		return {
			'status': 'success',
			'challenge': challenge
		}

	def _handle_authenticate(self, message, addr):
		"""Verify a challenge response and issue an auth token"""
		schema_errors = validate_message(message, {'client_nonce': str, 'server_nonce': str, 'proof': str})
		if schema_errors:
			return error_response('auth_failed', '; '.join(schema_errors))

		if self.security._is_auth_blocked(addr[0]):
			return error_response('auth_blocked', 'Too many failed authentication attempts; try again later')

		client_nonce = message['client_nonce']
		server_nonce = message['server_nonce']
		proof = message['proof']

		challenge = self.security.consume_challenge(client_nonce, server_nonce, addr[0])
		if not challenge:
			self.security._record_auth_failure(addr[0])
			return error_response('auth_failed', 'Invalid or expired authentication challenge')

		expected_proof = self.security.build_auth_proof(
			self.stored_password_hash,
			client_nonce,
			server_nonce
		)
		if not hmac.compare_digest(proof, expected_proof):
			self.security._record_auth_failure(addr[0])
			return error_response('auth_failed', 'Invalid authentication response')

		auth_token = self.security.issue_auth_token(addr[0])
		return {
			'status': 'success',
			'auth_token': auth_token,
			'expires_in': AUTH_TOKEN_TIMEOUT
		}

	def _require_authenticated(self, message, addr):
		"""Require a valid auth token for all protected TCP routes"""
		auth_token = message.get('auth_token')
		if not auth_token or not self.security.verify_auth_token(auth_token, addr[0]):
			return error_response('authentication_required', 'Authentication required')
		return None

	def _handle_get_pending_files(self, message):
		"""Backward-compatible view of current output manifest entries"""
		try:
			global render_manager
			if render_manager and render_manager.output_file_monitor:
				response_files = list(render_manager.output_file_monitor.get_output_manifest().values())
				json.dumps(response_files)
				return {'status': 'success', 'pending_files': response_files}
			else:
				return {'status': 'success', 'pending_files': []}
				
		except Exception as e:
			print(f"Get pending files request failed: {e}")
			return error_response('pending_files_failed', 'Failed to get pending files')

	def _handle_get_output_manifest(self, message):
		"""Return the current target-side output manifest"""
		try:
			global render_manager
			if render_manager and render_manager.output_file_monitor:
				manifest = render_manager.output_file_monitor.get_output_manifest()
				json.dumps(manifest)
				return {'status': 'success', 'manifest': manifest}
			return {'status': 'success', 'manifest': {}}

		except Exception as e:
			print(f"Get output manifest request failed: {e}")
			return error_response('output_manifest_failed', 'Failed to get output manifest')
	
	def _handle_request_file(self, message, client_sock):
		"""Handle request for a specific file - with better error handling"""
		try:
			relative_path = message.get('relative_path')
			if not relative_path:
				return {'status': 'error', 'message': 'File path required'}

			global render_manager
			if not render_manager or not render_manager.output_file_monitor:
				return {'status': 'error', 'message': 'No output files available'}

			normalized_relative_path = normalize_relative_path(relative_path)
			output_manifest = render_manager.output_file_monitor.get_output_manifest()
			manifest_entry = output_manifest.get(normalized_relative_path)
			if not manifest_entry:
				return {'status': 'error', 'message': 'Output file not available'}

			output_root = render_manager.output_file_monitor.project_root
			file_path = resolve_under_root(output_root, normalized_relative_path)

			if not os.path.isfile(file_path):
				print(f"File not found for request: {normalized_relative_path}")
				return {'status': 'error', 'message': 'File not found'}
			
			file_size = validate_file_size(os.path.getsize(file_path))

			# Send file info first
			response = {
				'status': 'success',
				'message': 'Sending file',
				'file_size': file_size,
				'relative_path': normalized_relative_path,
				'hash': manifest_entry.get('hash')
			}

			send_message(client_sock, response)

			# Send file data
			print(f"Sending file: {normalized_relative_path} ({file_size} bytes)")
			send_file(client_sock, file_path, file_size)
			
			print(f"File sent successfully: {normalized_relative_path}")
			
			return None  # Response already sent
			
		except PathSecurityError:
			return {'status': 'error', 'message': 'Invalid file path'}
		except ProtocolError as e:
			print(f"File request protocol failed: {e}")
			return error_response('protocol_error', str(e))
		except Exception as e:
			print(f"File request failed: {e}")
			return error_response('file_request_failed', 'File request failed')
	
	def _handle_get_manifest(self, message):
		"""Handle request for project manifest"""
		try:
			project_name = message.get('project_name', 'default')
			project_cache_dir, _project_id = self._get_project_cache_dir(project_name)
			manifest = self._load_input_manifest(project_cache_dir)
			return {'status': 'success', 'manifest': manifest}

		except PathSecurityError:
			return {'status': 'error', 'message': 'Invalid project path'}
		except Exception as e:
			print(f"Manifest request failed: {e}")
			return error_response('manifest_failed', 'Failed to get manifest')
	
	def _handle_sync_file(self, message, client_sock):
		"""Handle file synchronization request"""
		try:
			project_name = message.get('project_name', 'default')
			file_path = message.get('file_path')
			file_size = validate_file_size(message.get('file_size', 0))
			manifest_entry = file_sync_manager.sanitize_manifest_entry(message.get('manifest_entry', {}))
			
			if not file_path:
				return {'status': 'error', 'message': 'File path required'}

			relative_path = normalize_relative_path(file_path)
			if is_reserved_input_manifest_path(relative_path):
				return {'status': 'error', 'message': 'Invalid file path'}

			project_cache_dir, _project_id = self._get_project_cache_dir(project_name)
			target_file_path = resolve_under_root(project_cache_dir, relative_path)
			
			# Create directory if needed
			os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
			
			recv_file(client_sock, target_file_path, file_size)

			try:
				stat = os.stat(target_file_path)
			except FileNotFoundError:
				return error_response('file_sync_failed', 'File vanished after receive')
			manifest_entry['size'] = stat.st_size
			manifest_entry.setdefault('mtime', stat.st_mtime)
			if not manifest_entry.get('hash'):
				file_hash = file_sync_manager.calculate_file_hash(target_file_path)
				if file_hash:
					manifest_entry['hash'] = file_hash

			manifest = self._load_input_manifest(project_cache_dir)
			manifest[relative_path] = manifest_entry
			self._write_input_manifest(project_cache_dir, manifest)

			return {'status': 'success', 'message': 'File received'}

		except PathSecurityError:
			return {'status': 'error', 'message': 'Invalid file path'}
		except ProtocolError as e:
			print(f"File sync protocol failed: {e}")
			return error_response('protocol_error', str(e))
		except Exception as e:
			print(f"File sync failed: {e}")
			return error_response('file_sync_failed', 'File sync failed')

	def _handle_delete_obsolete_inputs(self, message):
		"""Delete stale target inputs that are owned by the stored input manifest"""
		try:
			if self.is_rendering:
				return error_response('render_in_progress', 'Cannot delete inputs while rendering')

			project_name = message.get('project_name', 'default')
			requested_paths = message.get('paths', [])
			if not isinstance(requested_paths, list):
				return {'status': 'error', 'message': 'Invalid delete request'}

			project_cache_dir, _project_id = self._get_project_cache_dir(project_name)
			manifest = self._load_input_manifest(project_cache_dir)
			deleted_paths = []
			missing_paths = []
			skipped_paths = []

			for requested_path in requested_paths:
				try:
					relative_path = normalize_relative_path(requested_path)
				except PathSecurityError:
					skipped_paths.append(str(requested_path))
					continue

				if is_reserved_input_manifest_path(relative_path) or relative_path not in manifest:
					skipped_paths.append(relative_path)
					continue

				target_file_path = resolve_under_root(project_cache_dir, relative_path)
				if os.path.isfile(target_file_path) or os.path.islink(target_file_path):
					os.remove(target_file_path)
					deleted_paths.append(relative_path)
					self._remove_empty_parent_dirs(project_cache_dir, target_file_path)
				elif not os.path.exists(target_file_path):
					missing_paths.append(relative_path)
				else:
					skipped_paths.append(relative_path)
					continue

				manifest.pop(relative_path, None)

			self._write_input_manifest(project_cache_dir, manifest)
			return {
				'status': 'success',
				'deleted_paths': deleted_paths,
				'missing_paths': missing_paths,
				'skipped_paths': skipped_paths
			}

		except PathSecurityError:
			return {'status': 'error', 'message': 'Invalid project path'}
		except Exception as e:
			print(f"Obsolete input delete failed: {e}")
			return error_response('delete_failed', 'Failed to delete obsolete inputs')
			
	def _validate_render_settings(self, settings):
		"""Validate remote-supplied render settings. Raises ValueError listing all violations."""
		errors = []

		for key in ('resolution_x', 'resolution_y'):
			if key in settings:
				val = settings[key]
				if not isinstance(val, int) or not (1 <= val <= 16384):
					errors.append(f"{key} must be an integer in [1, 16384]")

		if 'resolution_percentage' in settings:
			val = settings['resolution_percentage']
			if not isinstance(val, int) or not (1 <= val <= 100):
				errors.append("resolution_percentage must be an integer in [1, 100]")

		if 'engine' in settings:
			engine = settings['engine']
			if not isinstance(engine, str) or not re.match(r'^[A-Z][A-Z0-9_]*$', engine):
				errors.append("engine must be a valid uppercase identifier (e.g. 'CYCLES')")

		for key in ('frame_start', 'frame_end', 'frame_current'):
			if key in settings:
				val = settings[key]
				if not isinstance(val, int) or not (-1_000_000 <= val <= 1_000_000):
					errors.append(f"{key} must be an integer in [-1000000, 1000000]")

		if 'frame_step' in settings:
			val = settings['frame_step']
			if not isinstance(val, int) or not (1 <= val <= 10_000):
				errors.append("frame_step must be a positive integer in [1, 10000]")

		if 'output_path' in settings:
			errors.append("'output_path' is not allowed; use 'output_relative_path'")

		if 'output_relative_path' in settings:
			try:
				normalize_relative_path(settings['output_relative_path'])
			except PathSecurityError as e:
				errors.append(f"output_relative_path: {e}")

		if errors:
			raise ValueError("; ".join(errors))

	def _handle_render_request(self, message, addr):
		"""Handle render request from source computer"""
		try:
			schema_errors = validate_message(message, {'blend_file': str})
			if schema_errors:
				return error_response('invalid_request', '; '.join(schema_errors))

			if self.is_rendering:
				return error_response('render_in_progress', 'A render is already in progress')

			render_settings = message.get('render_settings', {})
			if not isinstance(render_settings, dict):
				return error_response('invalid_request', 'render_settings must be an object')

			try:
				self._validate_render_settings(render_settings)
			except ValueError as e:
				return error_response('invalid_render_settings', str(e))

			project_name = message.get('project_name', 'default')
			blend_file = message['blend_file']
			source_project_root = message.get('source_project_root')

			blend_file_rel = normalize_relative_path(blend_file)
			project_cache_dir, _project_id = self._get_project_cache_dir(project_name)
			blend_file_path = resolve_under_root(project_cache_dir, blend_file_rel)

			if source_project_root:
				render_settings['source_project_root'] = source_project_root

			self.is_rendering = True
			result = render_manager.start_render(blend_file_path, render_settings, source_project_root)
			return result

		except PathSecurityError:
			return {'status': 'error', 'message': 'Invalid render path'}
		except Exception as e:
			print(f"Render request failed: {e}")
			self.is_rendering = False
			return error_response('render_request_failed', 'Render request failed')
			
	def _handle_render_status_request(self, message):
		"""Handle request for render status"""
		try:
			status = render_manager.get_render_status()
			return {'status': 'success', 'render_status': status}
		except Exception as e:
			print(f"Status request failed: {e}")
			return error_response('status_request_failed', 'Status request failed')
			
	def _handle_render_cancel(self, message):
		"""Handle render cancellation request"""
		try:
			render_manager.cancel_render()
			self.is_rendering = False
			return {'status': 'success', 'message': 'Render cancelled'}
		except Exception as e:
			print(f"Cancel request failed: {e}")
			return error_response('cancel_request_failed', 'Cancel request failed')
	
	def _get_local_ip(self):
		"""Get local IP address"""
		try:
			with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
				s.connect(("8.8.8.8", 80))
				return s.getsockname()[0]
		except Exception:
			return "127.0.0.1"

	def _get_broadcast_addresses(self):
		"""Enumerate host network interfaces and return their broadcast addresses."""
		addrs = set()
		try:
			for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
				ip_str = info[4][0]
				if ip_str.startswith('127.'):
					continue
				try:
					ip_obj = ipaddress.ip_address(ip_str)
					for net in LAN_ALLOWED_NETWORKS:
						if net.version == 4 and ip_obj in net:
							parts = ip_str.split('.')
							addrs.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
							break
				except ValueError:
					pass
		except Exception:
			pass
		addrs.add('255.255.255.255')  # fallback global broadcast
		return sorted(addrs)
	
	# Client-side methods for source computer
	def discover_nodes(self, timeout=3):
		"""Discover available nodes on the network"""
		if not bpy.app.online_access:
			print("Render Remote: network access is disabled in Blender preferences — cannot discover nodes")
			return {}

		discovered = {}

		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
			sock.settimeout(DISCOVERY_BROADCAST_TIMEOUT)
			
			request = {
				'type': 'discovery_request',
				'timestamp': time.time()
			}
			
			request_data = json.dumps(request).encode()
			
			broadcast_addresses = self._get_broadcast_addresses()
			
			for broadcast_addr in broadcast_addresses:
				try:
					sock.sendto(request_data, (broadcast_addr, self.discovery_port))
				except OSError:
					continue
				
			# Collect responses
			start_time = time.time()
			while time.time() - start_time < timeout:
				try:
					data, addr = sock.recvfrom(1024)
					if not self._is_allowed_peer(addr[0]):
						continue

					response = json.loads(data.decode())

					if response.get('type') == 'discovery_response':
						if not self._is_allowed_peer(response.get('ip')):
							continue

						node_id = f"{response['ip']}:{response['port']}"
						discovered[node_id] = {
							'name': response['node_name'],
							'ip': response['ip'],
							'port': response['port'],
							'blender_version': response['blender_version'],
							'plugin_version': response['plugin_version'],
							'requires_auth': response['requires_auth'],
							'fingerprint': response.get('fingerprint'),
							'last_seen': time.time()
						}

				except socket.timeout:
					continue
				except Exception as e:
					print(f"Discovery error: {e}")
					
		except Exception as e:
			print(f"Failed to discover nodes: {e}")
		finally:
			try:
				sock.close()
			except OSError:
				pass

		self.discovered_nodes = discovered
		return discovered

	def _send_request(self, ip, port, request, timeout=10):
		"""Send a request message and receive a response message"""
		with self._create_connection(ip, port, timeout=timeout) as sock:
			send_message(sock, request)
			return recv_message(sock)

	def test_connection(self, ip, port, auth_token=None):
		"""Test connection to a remote node"""
		try:
			test_message = {
				'type': 'connection_test',
				'auth_token': auth_token,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, test_message, timeout=5)
			return response.get('status') == 'success'

		except Exception as e:
			print(f"Connection test failed: {e}")
			return False
	
	def authenticate(self, ip, port, password):
		"""Authenticate with a remote node"""
		try:
			challenge_request = {
				'type': 'auth_challenge',
				'timestamp': time.time()
			}
			challenge_response = self._send_request(ip, port, challenge_request, timeout=5)
			if challenge_response.get('status') != 'success':
				return None

			challenge = challenge_response.get('challenge', {})
			client_nonce = challenge.get('client_nonce')
			server_nonce = challenge.get('server_nonce')
			salt = challenge.get('salt')
			iterations = challenge.get('iterations', AUTH_PBKDF2_ITERATIONS)
			if not client_nonce or not server_nonce or not salt:
				return None

			password_hash = hashlib.pbkdf2_hmac(
				'sha256',
				password.encode(),
				salt.encode(),
				int(iterations)
			)
			proof = self.security.build_auth_proof(password_hash, client_nonce, server_nonce)
			auth_message = {
				'type': 'authenticate',
				'client_nonce': client_nonce,
				'server_nonce': server_nonce,
				'proof': proof,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, auth_message, timeout=5)
			if response.get('status') == 'success':
				return response.get('auth_token')

		except Exception as e:
			print(f"Authentication failed: {e}")
			
		return None
	
	def get_remote_manifest(self, ip, port, auth_token, project_name):
		"""Get project manifest from remote node"""
		try:
			request = {
				'type': 'get_project_manifest',
				'auth_token': auth_token,
				'project_name': project_name,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, request, timeout=10)
			if response.get('status') == 'success':
				return response.get('manifest', {})

		except Exception as e:
			print(f"Failed to get remote manifest: {e}")
			
		return None
	
	def sync_file_to_remote(self, ip, port, auth_token, project_name, file_path, local_file_path, manifest_entry=None):
		"""Sync a file to remote node"""
		try:
			relative_path = normalize_relative_path(file_path)
			if is_reserved_input_manifest_path(relative_path):
				return False
			file_size = validate_file_size(os.path.getsize(local_file_path))
			manifest_entry = file_sync_manager.sanitize_manifest_entry(manifest_entry or {})

			with self._create_connection(ip, port, timeout=30) as sock:
				request = {
					'type': 'sync_file',
					'auth_token': auth_token,
					'project_name': project_name,
					'file_path': relative_path,
					'file_size': file_size,
					'manifest_entry': manifest_entry,
					'timestamp': time.time()
				}

				send_message(sock, request)
				send_file(sock, local_file_path, file_size)
				response = recv_message(sock)

				return response.get('status') == 'success'

		except Exception as e:
			print(f"File sync failed: {e}")
			return False

	def delete_obsolete_inputs(self, ip, port, auth_token, project_name, paths):
		"""Ask the target to delete stale inputs it owns in its stored manifest"""
		try:
			normalized_paths = []
			for path in paths:
				normalized_path = normalize_relative_path(path)
				if not is_reserved_input_manifest_path(normalized_path):
					normalized_paths.append(normalized_path)

			if not normalized_paths:
				return {'status': 'success', 'deleted_paths': [], 'missing_paths': [], 'skipped_paths': []}

			request = {
				'type': 'delete_obsolete_inputs',
				'auth_token': auth_token,
				'project_name': project_name,
				'paths': sorted(set(normalized_paths)),
				'timestamp': time.time()
			}
			return self._send_request(ip, port, request, timeout=30)

		except Exception as e:
			print(f"Delete obsolete inputs failed: {e}")
			return None
	
	def send_render_request(self, ip, port, auth_token, project_name, blend_file, render_settings, source_project_root=None):
		"""Send render request to remote node"""
		try:
			blend_file = normalize_relative_path(blend_file)
			request = {
				'type': 'render_request',
				'auth_token': auth_token,
				'project_name': project_name,
				'blend_file': blend_file,
				'render_settings': render_settings,
				'timestamp': time.time()
			}
			if source_project_root:
				request['source_project_root'] = source_project_root
			return self._send_request(ip, port, request, timeout=30)

		except Exception as e:
			print(f"Render request failed: {e}")
		return None
			
	def get_render_status(self, ip, port, auth_token):
		"""Get render status from remote node"""
		try:
			request = {
				'type': 'render_status',
				'auth_token': auth_token,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, request, timeout=10)
			if response.get('status') == 'success':
				return response.get('render_status')

		except Exception as e:
			print(f"Render status request failed: {e}")
			
		return None
		
	def get_pending_files(self, ip, port, auth_token):
		"""Get list of files pending sync from remote node"""
		try:
			request = {
				'type': 'get_pending_files',
				'auth_token': auth_token,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, request, timeout=10)
			if response.get('status') == 'success':
				return response.get('pending_files', [])

		except Exception as e:
			print(f"Get pending files request failed: {e}")
			
		return []

	def get_output_manifest(self, ip, port, auth_token):
		"""Get the current output manifest from the target node"""
		try:
			request = {
				'type': 'get_output_manifest',
				'auth_token': auth_token,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, request, timeout=10)
			if response.get('status') == 'success':
				return response.get('manifest', {})

		except Exception as e:
			print(f"Get output manifest request failed: {e}")

		return None
	
	def request_file_from_target(self, ip, port, auth_token, relative_path, manifest_entry=None):
		"""Request and verify a specific output file from the target computer"""
		relative_path = normalize_relative_path(relative_path)
		manifest_entry = manifest_entry or {}
		delays = [0, 1, 2, 4]
		last_error = None
		for attempt, delay in enumerate(delays):
			if delay:
				time.sleep(delay)
			try:
				with self._create_connection(ip, port, timeout=30) as sock:
					request = {
						'type': 'request_file',
						'auth_token': auth_token,
						'relative_path': relative_path,
						'timestamp': time.time()
					}

					send_message(sock, request)
					response = recv_message(sock)

					if response.get('status') != 'success':
						print(f"File request failed: {response.get('message', 'Unknown error')}")
						return False

					file_size = validate_file_size(response.get('file_size', 0))
					target_relative_path = normalize_relative_path(response.get('relative_path', relative_path))
					expected_hash = manifest_entry.get('hash') or response.get('hash')

					print(f"Receiving file: {target_relative_path} ({file_size} bytes)")

					source_project_root = file_sync_manager.get_project_root()
					if not source_project_root:
						raise PathSecurityError("Could not determine source project root")

					target_path = resolve_under_root(source_project_root, target_relative_path)

					target_dir = os.path.dirname(target_path)
					os.makedirs(target_dir, exist_ok=True)

					download_path = f"{target_path}.download"
					recv_file(sock, download_path, file_size)

					if expected_hash:
						download_hash = file_sync_manager.calculate_file_hash(download_path)
						if download_hash != expected_hash:
							try:
								os.remove(download_path)
							except OSError:
								pass
							print(f"Downloaded file hash mismatch: {target_relative_path}")
							return False

					os.replace(download_path, target_path)
					print(f"✓ Successfully received: {target_relative_path}")
					return True

			except (PathSecurityError, ProtocolError):
				raise
			except Exception as e:
				last_error = e
				print(f"File request attempt {attempt + 1} failed: {e}")

		print(f"File request failed after {len(delays)} attempts: {last_error}")
		return False
	
	def cancel_remote_render(self, ip, port, auth_token):
		"""Cancel render on remote node"""
		try:
			request = {
				'type': 'render_cancel',
				'auth_token': auth_token,
				'timestamp': time.time()
			}
			response = self._send_request(ip, port, request, timeout=10)
			return response.get('status') == 'success'

		except Exception as e:
			print(f"Render cancel failed: {e}")
			return False

# ----
# Rendering Management (Simplified)
# ----

class RenderManager:
	"""Manages rendering operations on target computers"""
	
	def __init__(self):
		self.active_render = None
		self.render_thread = None
		self.render_progress = 0.0
		self.render_status = "idle"
		self.render_start_time = None
		self.render_error_message = ""
		self.frame_count = 0
		self.current_frame = 0
		self.output_paths = []
		self.original_output_path = ""
		self.render_queue = []
		self._handlers_registered = False
		self.output_file_monitor = None
		
	def start_render(self, blend_file_path, render_settings, source_project_root):
		"""Queue a render request"""
		if self.active_render and self.render_status in ['preparing', 'rendering']:
			return {'status': 'error', 'message': 'Render already in progress'}
		
		render_request = {
			'blend_file_path': blend_file_path,
			'render_settings': render_settings,
			'source_project_root': source_project_root,
			'timestamp': time.time()
		}
		
		self.render_queue.append(render_request)
		
		# Process the render request using timer
		def process_render():
			global network_manager
			if not self.render_queue:
				return None
				
			render_request = self.render_queue.pop(0)
			
			try:
				self._execute_render_request(render_request)
			except Exception as e:
				print(f"Render execution failed: {e}")
				self.render_status = "error"
				self.render_error_message = str(e)
				self.active_render = False
				network_manager.is_rendering = False
			
			return None
		
		timer_manager.register_timer(process_render, interval=0.1)
		
		return {'status': 'success', 'message': 'Render queued'}
	
	def _execute_render_request(self, render_request):
		"""Execute render request on main thread"""
		blend_file_path = render_request['blend_file_path']
		render_settings = render_request['render_settings']
		source_project_root = render_request['source_project_root']
		
		# Ensure blend file exists
		if not os.path.exists(blend_file_path):
			raise Exception(f'Blend file not found: {blend_file_path}')

		if bpy.data.is_dirty:
			raise Exception("Cannot load remote blend file: current file has unsaved changes")

		try:
			print(f"Loading blend file: {blend_file_path}")
			# Temporarily disable cleanup during file loading
			global network_manager
			was_rendering = network_manager.is_rendering
			network_manager.is_rendering = True

			bpy.ops.wm.open_mainfile(filepath=blend_file_path)

			# Keep rendering flag set if it was set before
			network_manager.is_rendering = was_rendering

			print("Blend file loaded successfully")
		except RuntimeError as e:
			raise Exception(f"Failed to load blend file: {e}")
		except Exception as e:
			raise Exception(f"Failed to load blend file: {e}")
		
		# Apply render settings
		self._apply_render_settings(render_settings)
		
		# Set up progress monitoring
		self._setup_render_monitoring()
		
		# Set up output file monitoring for automatic sync
		self._setup_output_file_monitoring(source_project_root)
		
		# Start render
		self.render_status = "preparing"
		self.render_start_time = time.time()
		self.active_render = True
		
		if render_settings.get('animation', False):
			self._start_animation_render()
		else:
			self._start_still_render()
			
	def _apply_render_settings(self, settings):
		"""Apply render settings to scene"""
		scene = bpy.context.scene
		
		# Store original output path
		self.original_output_path = scene.render.filepath
		
		# Frame range for animation
		if 'frame_start' in settings:
			scene.frame_start = settings['frame_start']
		if 'frame_end' in settings:
			scene.frame_end = settings['frame_end']
		if 'frame_current' in settings:
			scene.frame_current = settings['frame_current']
			
		# Output settings
		if 'output_relative_path' in settings:
			project_root = os.path.dirname(os.path.dirname(bpy.data.filepath))
			scene.render.filepath = resolve_under_root(project_root, settings['output_relative_path'])
		if 'file_format' in settings:
			scene.render.image_settings.file_format = settings['file_format']
		if 'resolution_x' in settings:
			scene.render.resolution_x = settings['resolution_x']
		if 'resolution_y' in settings:
			scene.render.resolution_y = settings['resolution_y']
		if 'resolution_percentage' in settings:
			scene.render.resolution_percentage = settings['resolution_percentage']
			
		# Engine specific settings
		if 'engine' in settings:
			scene.render.engine = settings['engine']
			
		# Calculate frame count for animation
		if settings.get('animation', False):
			self.frame_count = scene.frame_end - scene.frame_start + 1
		else:
			self.frame_count = 1
	
	def _setup_output_file_monitoring(self, source_project_root):
		"""Set up monitoring for newly created files during rendering"""
		# Get the project root directory (parent of blend file)
		blend_file_path = bpy.data.filepath
		if not blend_file_path:
			return
			
		project_root = os.path.dirname(os.path.dirname(blend_file_path))
		
		# Create a file monitor - SIMPLIFIED VERSION
		self.output_file_monitor = OutputFileMonitor(project_root, source_project_root)
		self.output_file_monitor.start_monitoring()
			
	def _setup_render_monitoring(self):
		"""Set up render progress monitoring"""
		# Clear previous handlers
		self._clear_render_handlers()
		
		# Add render handlers only if not already registered
		if not self._handlers_registered:
			bpy.app.handlers.render_pre.append(_render_pre_handler)
			bpy.app.handlers.render_post.append(_render_post_handler)
			bpy.app.handlers.render_cancel.append(_render_cancel_handler)
			bpy.app.handlers.render_complete.append(_render_complete_handler)
			bpy.app.handlers.render_write.append(_render_write_handler)
			self._handlers_registered = True
	
	def _clear_render_handlers(self):
		"""Remove all render handlers"""
		if not self._handlers_registered:
			return
			
		handlers_to_remove = [
			(bpy.app.handlers.render_pre, _render_pre_handler),
			(bpy.app.handlers.render_post, _render_post_handler),
			(bpy.app.handlers.render_cancel, _render_cancel_handler),
			(bpy.app.handlers.render_complete, _render_complete_handler),
			(bpy.app.handlers.render_write, _render_write_handler),
		]
		
		for handler_list, handler_func in handlers_to_remove:
			if handler_func in handler_list:
				handler_list.remove(handler_func)
		
		self._handlers_registered = False
		
	def _start_still_render(self):
		"""Start still image render"""
		def render_callback():
			global network_manager
			try:
				self.render_status = "rendering"
				bpy.ops.render.render('INVOKE_DEFAULT')
			except Exception as e:
				self.render_status = "error"
				self.render_error_message = str(e)
				self.active_render = False
				network_manager.is_rendering = False
			return None
		
		timer_manager.register_timer(render_callback, interval=0.1)
		
	def _start_animation_render(self):
		"""Start animation render"""
		def render_callback():
			global network_manager
			try:
				self.render_status = "rendering"
				bpy.ops.render.render('INVOKE_DEFAULT', animation=True)
			except Exception as e:
				self.render_status = "error"
				self.render_error_message = str(e)
				self.active_render = False
				network_manager.is_rendering = False
			return None
		
		timer_manager.register_timer(render_callback, interval=0.1)
		
	def cancel_render(self):
		"""Cancel active render"""
		if self.active_render:
			def cancel_callback():
				global network_manager
				try:
					self.render_status = "cancelled"
					self.active_render = False
					network_manager.is_rendering = False
				except Exception:
					self.render_status = "cancelled"
					self.active_render = False
					network_manager.is_rendering = False
				return None
			
			timer_manager.register_timer(cancel_callback, interval=0.1)
		
		# Stop output file monitoring
		if self.output_file_monitor:
			self.output_file_monitor.stop_monitoring()
			self.output_file_monitor = None
				
		self._clear_render_handlers()
		
	def get_render_status(self):
		"""Get current render status"""
		elapsed_time = 0
		if self.render_start_time:
			elapsed_time = time.time() - self.render_start_time
			
		return {
			'status': self.render_status,
			'progress': self.render_progress,
			'current_frame': self.current_frame,
			'frame_count': self.frame_count,
			'elapsed_time': elapsed_time,
			'error_message': self.render_error_message
		}
	
	def cleanup(self):
		"""Clean up render manager resources"""
		global network_manager
		self.render_queue.clear()
		self.active_render = False
		
		# Stop output file monitoring
		if self.output_file_monitor:
			self.output_file_monitor.stop_monitoring()
			self.output_file_monitor = None
			
		self._clear_render_handlers()
		
		# Reset rendering flag
		network_manager.is_rendering = False

# Global instances
render_manager = RenderManager()
network_manager = NetworkManager()

# ----
# Property Groups for UI State
# ----

def update_remote_mode(self, context):
	"""Ensure source mode does not keep a listening target service running"""
	try:
		if self.mode != 'TARGET':
			if network_manager.discovery_active:
				network_manager.stop_discovery_server(force=True)
			elif network_manager.communication_active:
				network_manager.stop_communication_server(force=True)
	except Exception as e:
		print(f"Remote mode update failed: {e}")

class SyncFileInfo(PropertyGroup):
	"""Information about a file that needs syncing"""
	file_path: StringProperty()
	status: StringProperty()  # 'new', 'modified', 'deleted', 'external', 'missing'
	size: IntProperty()
	selected: BoolProperty(default=True)

class RemoteNodeProperties(PropertyGroup):
	"""Properties for remote node information"""
	
	node_id: StringProperty(name="Node ID")
	name: StringProperty(name="Node Name")
	ip: StringProperty(name="IP Address")
	port: IntProperty(name="Port")
	blender_version: StringProperty(name="Blender Version")
	plugin_version: StringProperty(name="Plugin Version")
	requires_auth: BoolProperty(name="Requires Authentication")
	is_connected: BoolProperty(name="Is Connected")
	auth_token: StringProperty(name="Auth Token")
	
class RemoteRenderProperties(PropertyGroup):
	"""Main properties for remote render settings"""
	
	# Mode selection
	mode: EnumProperty(
		name="Mode",
		description="Select operation mode",
		items=[
			('SOURCE', "Source", "Control remote rendering from this computer"),
			('TARGET', "Target", "Allow this computer to be used for remote rendering")
		],
		default='SOURCE',
		update=update_remote_mode
	)
	
	# Node name for target mode
	node_name: StringProperty(
		name="Node Name",
		description="Name for this computer when discovered",
		default=socket.gethostname()
	)
	
	# Connection settings for source mode
	selected_node: StringProperty(
		name="Selected Node",
		description="Currently selected remote node"
	)
	
	manual_ip: StringProperty(
		name="Manual IP",
		description="Manually enter IP address",
		default=""
	)
	
	manual_port: IntProperty(
		name="Manual Port",
		description="Port for manual connection",
		default=5002,
		min=1024,
		max=65535
	)
	
	connection_password: StringProperty(
		name="Connection Password",
		description="Password for connecting to remote node",
		subtype='PASSWORD',
		default=""
	)
	
	# Project settings
	project_name: StringProperty(
		name="Project Name",
		description="Name for the project on the remote cache",
		default="Untitled"
	)
	
	# Sync status
	sync_status: StringProperty(
		name="Sync Status",
		default="Not Scanned"
	)
	
	external_files_count: IntProperty(
		name="External Files Count",
		default=0
	)
	
	show_external_warning: BoolProperty(
		name="Show External Warning",
		default=False
	)

	missing_files_count: IntProperty(
		name="Missing Files Count",
		default=0
	)

	show_missing_warning: BoolProperty(
		name="Show Missing Warning",
		default=False
	)
	
	# Render status properties
	render_status: StringProperty(
		name="Render Status",
		default="Not Started"
	)
	
	render_progress: FloatProperty(
		name="Render Progress",
		default=0.0,
		min=0.0,
		max=100.0,
		subtype='PERCENTAGE'
	)
	
	current_frame: IntProperty(
		name="Current Frame",
		default=0
	)
	
	total_frames: IntProperty(
		name="Total Frames",
		default=0
	)
	
	render_elapsed_time: FloatProperty(
		name="Elapsed Time",
		default=0.0
	)
	
	render_error_message: StringProperty(
		name="Render Error",
		default=""
	)
	
	# Progress monitoring
	monitor_render: BoolProperty(
		name="Monitor Render",
		default=False
	)

# ----
# Source-side Render Remote Workflow Helpers
# ----

def get_connected_remote_node(context, props):
	"""Return the selected connected remote node, if any"""
	for node in context.scene.discovered_nodes:
		if node.node_id == props.selected_node and node.is_connected:
			return node
	return None

def format_connected_remote_label(node):
	"""Human-friendly label for the connected render target"""
	if not node:
		return "Not connected"
	if node.name and node.ip:
		return f"{node.name} ({node.ip}:{node.port})"
	if node.name:
		return node.name
	if node.ip:
		return f"{node.ip}:{node.port}"
	return "Connected target"

def update_sync_ui_from_scan(context, dependencies, sync_changes=None):
	"""Update sync UI state from dependency and manifest comparison results"""
	props = context.scene.remote_render_props
	props.external_files_count = len(dependencies['external'])
	props.show_external_warning = len(dependencies['external']) > 0
	props.missing_files_count = len(dependencies['missing'])
	props.show_missing_warning = len(dependencies['missing']) > 0

	context.scene.sync_files.clear()

	if sync_changes:
		for file_info in sync_changes['new_files']:
			item = context.scene.sync_files.add()
			item.file_path = file_info['path']
			item.status = 'new'
			item.size = file_info['size']

		for file_info in sync_changes['modified_files']:
			item = context.scene.sync_files.add()
			item.file_path = file_info['path']
			item.status = 'modified'
			item.size = file_info['size']

		for file_info in sync_changes['deleted_files']:
			item = context.scene.sync_files.add()
			item.file_path = file_info['path']
			item.status = 'deleted'
			item.size = 0

		total_files = len(sync_changes['new_files']) + len(sync_changes['modified_files'])
		if total_files:
			props.sync_status = f"{total_files} files need sync"
		elif dependencies['external'] or dependencies['missing']:
			props.sync_status = "Unsupported references found"
		elif sync_changes['deleted_files']:
			props.sync_status = f"{len(sync_changes['deleted_files'])} stale remote files"
		else:
			props.sync_status = "Up to date"
	else:
		props.sync_status = "Unsupported references found" if (dependencies['external'] or dependencies['missing']) else "Up to date"

	for file_path in dependencies['external']:
		item = context.scene.sync_files.add()
		item.file_path = file_path
		item.status = 'external'
		item.size = 0
		item.selected = False

	for file_path in dependencies['missing']:
		item = context.scene.sync_files.add()
		item.file_path = file_path
		item.status = 'missing'
		item.size = 0
		item.selected = False

def collect_project_sync_state(props, target_node, require_remote_manifest=True):
	"""Scan dependencies and compare them with the target-owned input manifest"""
	project_root = file_sync_manager.get_project_root()
	if not project_root:
		raise Exception("Could not determine project root")

	project_cache_name = build_source_project_cache_name(props.project_name)
	dependencies = file_sync_manager.scan_blend_dependencies()
	local_manifest = file_sync_manager.get_referenced_files_manifest(project_root, dependencies)
	remote_manifest = network_manager.get_remote_manifest(
		target_node.ip,
		target_node.port,
		target_node.auth_token,
		project_cache_name
	)
	if remote_manifest is None:
		if require_remote_manifest:
			raise Exception("Could not load target input manifest")
		sync_changes = None
	else:
		sync_changes = file_sync_manager.compare_manifests(local_manifest, remote_manifest)

	return {
		'project_root': project_root,
		'project_cache_name': project_cache_name,
		'dependencies': dependencies,
		'local_manifest': local_manifest,
		'remote_manifest': remote_manifest,
		'sync_changes': sync_changes
	}

def sync_project_inputs_to_target(target_node, project_cache_name, project_root, local_manifest, sync_changes, upload_paths=None, delete_paths=None, status_callback=None):
	"""Upload changed inputs and delete obsolete target-owned inputs"""
	changed_upload_paths = [file_info['path'] for file_info in sync_changes['new_files'] + sync_changes['modified_files']]
	stale_delete_paths = [file_info['path'] for file_info in sync_changes['deleted_files']]
	upload_paths = [normalize_relative_path(path) for path in (changed_upload_paths if upload_paths is None else upload_paths)]
	delete_paths = [normalize_relative_path(path) for path in (stale_delete_paths if delete_paths is None else delete_paths)]

	upload_paths = sorted(set(path for path in upload_paths if path in local_manifest))
	delete_paths = sorted(set(path for path in delete_paths if path not in local_manifest and path in stale_delete_paths))

	result = {
		'uploaded': 0,
		'upload_total': len(upload_paths),
		'deleted': 0,
		'delete_total': len(delete_paths),
		'failed_uploads': [],
		'failed_deletes': []
	}

	if upload_paths and status_callback:
		status_callback("Uploading inputs...")

	for relative_path in upload_paths:
		local_file_path = resolve_under_root(project_root, relative_path)
		if not os.path.exists(local_file_path):
			result['failed_uploads'].append(relative_path)
			continue

		success = network_manager.sync_file_to_remote(
			target_node.ip,
			target_node.port,
			target_node.auth_token,
			project_cache_name,
			relative_path,
			local_file_path,
			local_manifest[relative_path]
		)

		if success:
			result['uploaded'] += 1
		else:
			result['failed_uploads'].append(relative_path)

	if delete_paths:
		if status_callback:
			status_callback("Deleting stale inputs...")
		delete_response = network_manager.delete_obsolete_inputs(
			target_node.ip,
			target_node.port,
			target_node.auth_token,
			project_cache_name,
			delete_paths
		)
		if delete_response and delete_response.get('status') == 'success':
			result['deleted'] = len(delete_response.get('deleted_paths', [])) + len(delete_response.get('missing_paths', []))
			result['failed_deletes'] = delete_response.get('skipped_paths', [])
		else:
			result['failed_deletes'] = delete_paths

	return result

def build_project_relative_render_settings(scene, animation, project_root):
	"""Build render settings without source-machine absolute output paths"""
	render_settings = {
		'animation': animation,
		'frame_start': scene.frame_start,
		'frame_end': scene.frame_end,
		'frame_current': scene.frame_current,
		'file_format': scene.render.image_settings.file_format,
		'resolution_x': scene.render.resolution_x,
		'resolution_y': scene.render.resolution_y,
		'resolution_percentage': scene.render.resolution_percentage,
		'engine': scene.render.engine,
		'output_path_mode': 'project_relative'
	}

	if scene.render.filepath:
		output_path = bpy.path.abspath(scene.render.filepath)
		render_settings['output_relative_path'] = relative_path_under_root(output_path, project_root)

	return render_settings

def schedule_remote_status_update(sync_status=None, render_status=None, render_error_message=None, monitor_render=None):
	"""Schedule a UI-safe status update on the main thread"""
	def update():
		context = bpy.context
		if not context or not hasattr(context, 'scene') or not hasattr(context.scene, 'remote_render_props'):
			return None

		props = context.scene.remote_render_props
		if sync_status is not None:
			props.sync_status = sync_status
		if render_status is not None:
			props.render_status = render_status
		if render_error_message is not None:
			props.render_error_message = sanitize_ui_message(render_error_message)
		if monitor_render is not None:
			props.monitor_render = monitor_render
		return None

	timer_manager.register_timer(update, interval=0.1)

def sanitize_ui_message(message):
	"""Remove token-like strings and absolute paths from UI-facing messages"""
	text = str(message or "")
	text = re.sub(r'[A-Za-z]:[\\/][^\s,;:]+', '[path]', text)
	text = re.sub(r'/(?:[^/\s:]+/)*[^/\s:]+', '[path]', text)
	text = re.sub(r'\b[a-f0-9]{24,}\b', '[token]', text, flags=re.IGNORECASE)
	return text

def format_render_status_label(status):
	"""Human-friendly render status labels for the UI"""
	status_key = str(status or "").strip().lower()
	mapping = {
		'not started': 'Not Started',
		'idle': 'Idle',
		'preparing': 'Preparing',
		'rendering': 'Rendering',
		'completed': 'Complete',
		'cancelled': 'Cancelled',
		'error': 'Error',
	}
	if status_key in mapping:
		return mapping[status_key]
	text = str(status or "").strip()
	return text.replace('_', ' ').title() if text else 'Unknown'

# ----
# Operators (keeping existing ones but simplifying some logic)
# ----

class REMOTERENDER_OT_StartDiscovery(Operator):
	bl_idname = "render_remote.start_discovery"
	bl_label = "Allow Remote Rendering"
	bl_description = "Start the LAN listening service that allows other computers to send remote render jobs"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		prefs = context.preferences.addons[__package__].preferences
		
		if network_manager.discovery_active:
			self.report({'WARNING'}, "Discovery already active")
			return {'CANCELLED'}
		
		if not prefs.remote_passcode:
			self.report({'ERROR'}, "Set a Render Remote authentication passcode in add-on preferences before starting target mode")
			return {'CANCELLED'}

		network_manager.update_ports_from_preferences()
		
		if not network_manager.start_discovery_server(
			props.node_name,
			prefs.remote_passcode
		):
			self.report({'ERROR'}, "Failed to start authenticated remote render target")
			return {'CANCELLED'}
		
		self.report({'INFO'}, f"Remote render target enabled for {props.node_name}")
		return {'FINISHED'}

class REMOTERENDER_OT_StopDiscovery(Operator):
	bl_idname = "render_remote.stop_discovery"
	bl_label = "Stop Allowing Remote Rendering"
	bl_description = "Stop the LAN listening service for incoming remote render jobs"
	
	def execute(self, context):
		network_manager.stop_discovery_server()
		self.report({'INFO'}, "Remote render target disabled")
		return {'FINISHED'}

class REMOTERENDER_OT_ScanNetwork(Operator):
	bl_idname = "render_remote.scan_network"
	bl_label = "Scan Network"
	bl_description = "Scan network for available remote render nodes"
	
	def execute(self, context):
		self.report({'INFO'}, "Scanning network for remote nodes...")
		
		def scan_network():
			discovered = network_manager.discover_nodes()
			
			def update_ui():
				context = bpy.context
				context.scene.discovered_nodes.clear()
				
				for node_id, node_info in discovered.items():
					item = context.scene.discovered_nodes.add()
					item.node_id = node_id
					item.name = node_info['name']
					item.ip = node_info['ip']
					item.port = node_info['port']
					item.blender_version = node_info['blender_version']
					item.requires_auth = node_info['requires_auth']
				
				return None
			
			timer_manager.register_timer(update_ui, interval=0.1)
		
		threading.Thread(target=scan_network, daemon=True).start()
		
		return {'FINISHED'}

class REMOTERENDER_OT_ConnectNode(Operator):
	bl_idname = "render_remote.connect_node"
	bl_label = "Connect to Node"
	bl_description = "Connect to selected remote node"
	
	node_id: StringProperty()
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		# Find the node to connect to
		target_node = None
		for node in context.scene.discovered_nodes:
			if node.node_id == self.node_id:
				target_node = node
				break
		
		if not target_node:
			self.report({'ERROR'}, "Node not found")
			return {'CANCELLED'}

		if not is_allowed_lan_ip(target_node.ip):
			self.report({'ERROR'}, "Remote node is not on an allowed LAN address")
			return {'CANCELLED'}
		
		if not props.connection_password:
			self.report({'ERROR'}, "Password required for this node")
			return {'CANCELLED'}
			
		auth_token = network_manager.authenticate(
			target_node.ip,
			target_node.port,
			props.connection_password
		)
			
		if not auth_token:
			self.report({'ERROR'}, "Authentication failed - check password")
			return {'CANCELLED'}
		
		# Test connection
		if network_manager.test_connection(target_node.ip, target_node.port, auth_token):
			target_node.is_connected = True
			target_node.auth_token = auth_token or ""
			props.selected_node = self.node_id
			
			self.report({'INFO'}, f"Connected to {target_node.name}")
		else:
			self.report({'ERROR'}, f"Failed to connect to {target_node.name}")
			return {'CANCELLED'}
		
		return {'FINISHED'}

class REMOTERENDER_OT_DisconnectNode(Operator):
	bl_idname = "render_remote.disconnect_node"
	bl_label = "Disconnect"
	bl_description = "Disconnect from remote node"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		# Find connected node and disconnect
		for node in context.scene.discovered_nodes:
			if node.node_id == props.selected_node:
				node.is_connected = False
				node.auth_token = ""
				break
		
		props.selected_node = ""
		props.sync_status = "Not Scanned"
		self.report({'INFO'}, "Disconnected from remote node")
		return {'FINISHED'}

class REMOTERENDER_OT_ConnectManual(Operator):
	bl_idname = "render_remote.connect_manual"
	bl_label = "Connect Manual"
	bl_description = "Connect to manually entered IP address"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		if not props.manual_ip:
			self.report({'ERROR'}, "Please enter an IP address")
			return {'CANCELLED'}

		if not is_allowed_lan_ip(props.manual_ip):
			self.report({'ERROR'}, "Manual IP must be a private, link-local, or loopback address")
			return {'CANCELLED'}
		
		if not props.connection_password:
			self.report({'ERROR'}, "Password required for remote render nodes")
			return {'CANCELLED'}
			
		auth_token = network_manager.authenticate(
			props.manual_ip,
			props.manual_port,
			props.connection_password
		)

		if not auth_token:
			self.report({'ERROR'}, "Authentication failed - check password")
			return {'CANCELLED'}
		
		if network_manager.test_connection(props.manual_ip, props.manual_port, auth_token):
			# Add manual connection to discovered nodes
			manual_node = context.scene.discovered_nodes.add()
			manual_node.node_id = f"{props.manual_ip}:{props.manual_port}"
			manual_node.name = f"Manual ({props.manual_ip})"
			manual_node.ip = props.manual_ip
			manual_node.port = props.manual_port
			manual_node.is_connected = True
			manual_node.auth_token = auth_token or ""
			manual_node.requires_auth = True
			
			props.selected_node = manual_node.node_id
			
			self.report({'INFO'}, f"Connected to {props.manual_ip}")
		else:
			self.report({'ERROR'}, f"Failed to connect to {props.manual_ip}")
			return {'CANCELLED'}
		
		return {'FINISHED'}

class REMOTERENDER_OT_ScanProject(Operator):
	bl_idname = "render_remote.scan_project"
	bl_label = "Scan Project Dependencies"
	bl_description = "Scan current project for all file dependencies and check sync status"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		if not bpy.data.filepath:
			self.report({'ERROR'}, "Please save your blend file first")
			return {'CANCELLED'}
		
		props.sync_status = "Scanning inputs..."
		self.report({'INFO'}, "Scanning project dependencies...")
		
		def scan_project():
			try:
				context = bpy.context
				props = context.scene.remote_render_props
				dependencies = file_sync_manager.scan_blend_dependencies()
				sync_changes = None
				
				if props.selected_node:
					target_node = get_connected_remote_node(context, props)

					if target_node:
						sync_state = collect_project_sync_state(props, target_node, require_remote_manifest=False)
						dependencies = sync_state['dependencies']
						sync_changes = sync_state['sync_changes']
				
				def update_ui():
					context = bpy.context
					update_sync_ui_from_scan(context, dependencies, sync_changes)
					return None
				
				timer_manager.register_timer(update_ui, interval=0.1)
			
			except Exception as e:
				print(f"Project scan failed: {e}")
				
				def update_error():
					context = bpy.context
					props = context.scene.remote_render_props
					props.sync_status = f"Scan failed: {sanitize_ui_message(e)}"
					return None
				
				timer_manager.register_timer(update_error, interval=0.1)
		
		threading.Thread(target=scan_project, daemon=True).start()
		
		return {'FINISHED'}

class REMOTERENDER_OT_SyncFiles(Operator):
	bl_idname = "render_remote.sync_files"
	bl_label = "Sync Selected Files"
	bl_description = "Sync selected files to remote node"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		if not props.selected_node:
			self.report({'ERROR'}, "No remote node connected")
			return {'CANCELLED'}

		if not bpy.data.filepath:
			self.report({'ERROR'}, "Please save your blend file first")
			return {'CANCELLED'}
		
		# Find connected node
		target_node = None
		for node in context.scene.discovered_nodes:
			if node.node_id == props.selected_node and node.is_connected:
				target_node = node
				break
		
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}
		
		# Get selected files and stale owned inputs
		selected_upload_paths = [
			normalize_relative_path(f.file_path)
			for f in context.scene.sync_files
			if f.selected and f.status in {'new', 'modified'}
		]
		selected_delete_paths = [
			normalize_relative_path(f.file_path)
			for f in context.scene.sync_files
			if f.selected and f.status == 'deleted'
		]
		
		if not selected_upload_paths and not selected_delete_paths:
			self.report({'WARNING'}, "No files selected for sync")
			return {'CANCELLED'}
		
		props.sync_status = "Scanning inputs..."
		self.report({'INFO'}, f"Syncing {len(selected_upload_paths)} files and deleting {len(selected_delete_paths)} stale inputs...")
		
		def sync_files():
			try:
				sync_state = collect_project_sync_state(props, target_node)
				sync_result = sync_project_inputs_to_target(
					target_node,
					sync_state['project_cache_name'],
					sync_state['project_root'],
					sync_state['local_manifest'],
					sync_state['sync_changes'],
					upload_paths=selected_upload_paths,
					delete_paths=selected_delete_paths,
					status_callback=lambda message: schedule_remote_status_update(sync_status=message)
				)
				
				def update_ui():
					context = bpy.context
					props = context.scene.remote_render_props
					if sync_result['delete_total']:
						props.sync_status = f"Synced {sync_result['uploaded']}/{sync_result['upload_total']} files, deleted {sync_result['deleted']}/{sync_result['delete_total']} stale inputs"
					else:
						props.sync_status = f"Synced {sync_result['uploaded']}/{sync_result['upload_total']} files"
					
					if sync_result['failed_uploads'] or sync_result['failed_deletes']:
						props.sync_status = f"{props.sync_status} with errors"
					else:
						props.sync_status = "Complete"

					if sync_result['uploaded'] > 0 or sync_result['deleted'] > 0:
						bpy.ops.render_remote.scan_project()
					
					return None
				
				timer_manager.register_timer(update_ui, interval=0.1)
			
			except Exception as e:
				print(f"File sync failed: {e}")
				
				def update_error():
					context = bpy.context
					props = context.scene.remote_render_props
					props.sync_status = f"Sync failed: {sanitize_ui_message(e)}"
					return None
				
				timer_manager.register_timer(update_error, interval=0.1)
		
		threading.Thread(target=sync_files, daemon=True).start()
		
		return {'FINISHED'}

class REMOTERENDER_OT_StartRemoteRender(Operator):
	bl_idname = "render_remote.start_remote_render"
	bl_label = "Start Remote Render"
	bl_description = "Start rendering on remote computer"
	
	animation: BoolProperty(name="Animation", default=False)
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		if not props.selected_node:
			self.report({'ERROR'}, "No remote node connected")
			return {'CANCELLED'}
		
		if not bpy.data.filepath:
			self.report({'ERROR'}, "Please save your blend file first")
			return {'CANCELLED'}
		
		# Find connected node
		target_node = None
		for node in context.scene.discovered_nodes:
			if node.node_id == props.selected_node and node.is_connected:
				target_node = node
				break
		
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}

		animation = self.animation
		props.sync_status = "Scanning inputs..."
		props.render_status = "preparing"
		props.monitor_render = False
		props.render_error_message = ""
		self.report({'INFO'}, "Preparing remote render: scanning and syncing inputs...")

		def start_render_workflow():
			try:
				context = bpy.context
				props = context.scene.remote_render_props
				scene = context.scene
				sync_state = collect_project_sync_state(props, target_node)
				dependencies = sync_state['dependencies']
				sync_changes = sync_state['sync_changes']

				def update_scanned():
					context = bpy.context
					props = context.scene.remote_render_props
					update_sync_ui_from_scan(context, dependencies, sync_changes)
					return None

				timer_manager.register_timer(update_scanned, interval=0.1)

				if dependencies['missing']:
					raise Exception("Referenced files are missing. Restore them and scan again before rendering remotely.")

				if dependencies['external']:
					raise Exception("External references outside the project root are not supported for remote rendering.")

				render_settings = build_project_relative_render_settings(scene, animation, sync_state['project_root'])
				blend_file_rel = relative_path_under_root(bpy.data.filepath, sync_state['project_root'])

				sync_result = sync_project_inputs_to_target(
					target_node,
					sync_state['project_cache_name'],
					sync_state['project_root'],
					sync_state['local_manifest'],
					sync_changes,
					status_callback=lambda message: schedule_remote_status_update(sync_status=message, render_status="preparing")
				)

				if sync_result['failed_uploads']:
					raise Exception(f"Failed to sync {len(sync_result['failed_uploads'])} input files")

				if sync_result['failed_deletes']:
					raise Exception(f"Failed to delete {len(sync_result['failed_deletes'])} stale remote inputs")

				def update_starting():
					context = bpy.context
					props = context.scene.remote_render_props
					props.sync_status = "Rendering..."
					props.render_status = "preparing"
					return None

				timer_manager.register_timer(update_starting, interval=0.1)

				result = network_manager.send_render_request(
					target_node.ip,
					target_node.port,
					target_node.auth_token,
					sync_state['project_cache_name'],
					blend_file_rel,
					render_settings,
					sync_state['project_root']
				)

				if not result or result.get('status') != 'success':
					error_msg = result.get('message', 'Unknown error') if result else 'Connection failed'
					raise Exception(f"Failed to start render: {error_msg}")

				def update_success():
					context = bpy.context
					props = context.scene.remote_render_props
					props.render_status = "preparing"
					props.monitor_render = True
					self.report({'INFO'}, "Render started on remote computer")
					self._start_progress_monitoring(context, target_node)
					return None

				timer_manager.register_timer(update_success, interval=0.1)

			except Exception as e:
				error_message = str(e)
				print(f"Remote render preparation failed: {error_message}")

				def update_error():
					context = bpy.context
					props = context.scene.remote_render_props
					props.render_status = "error"
					props.render_error_message = sanitize_ui_message(error_message)
					props.monitor_render = False
					props.sync_status = f"Render preparation failed: {sanitize_ui_message(error_message)}"
					self.report({'ERROR'}, sanitize_ui_message(error_message))
					return None

				timer_manager.register_timer(update_error, interval=0.1)

		threading.Thread(target=start_render_workflow, daemon=True).start()
		return {'FINISHED'}
	
	def _start_progress_monitoring(self, context, target_node):
		"""Start monitoring render progress and reconciling rendered outputs"""
		downloaded_hashes = {}
		last_manifest_signature = None
		last_manifest_change = time.time()
		completion_observed_at = None

		def monitor_progress():
			nonlocal last_manifest_signature, last_manifest_change, completion_observed_at
			props = context.scene.remote_render_props
			
			if not props.monitor_render:
				return None
			
			# Get render status
			status = network_manager.get_render_status(
				target_node.ip,
				target_node.port,
				target_node.auth_token
			)
			
			if status:
				props.render_status = status.get('status', 'Unknown')
				props.render_progress = status.get('progress', 0.0)
				props.current_frame = status.get('current_frame', 0)
				props.total_frames = status.get('frame_count', 0)
				props.render_elapsed_time = status.get('elapsed_time', 0.0)
				props.render_error_message = sanitize_ui_message(status.get('error_message', ''))
			
			manifest = None
			try:
				manifest = network_manager.get_output_manifest(
					target_node.ip,
					target_node.port,
					target_node.auth_token
				)
			except Exception as e:
				print(f"Error checking output manifest: {e}")
				manifest = None

			now = time.time()
			download_count = 0
			if manifest is not None:
				manifest_signature = tuple(
					(path, entry.get('hash'), entry.get('size'), entry.get('timestamp'))
					for path, entry in sorted(manifest.items())
				)
				if manifest_signature != last_manifest_signature:
					last_manifest_signature = manifest_signature
					last_manifest_change = now

				source_project_root = file_sync_manager.get_project_root()
				for relative_path, entry in sorted(manifest.items()):
					expected_hash = entry.get('hash')
					local_output_exists = False
					if source_project_root:
						try:
							local_output_exists = os.path.exists(resolve_under_root(source_project_root, relative_path))
						except PathSecurityError:
							local_output_exists = False
					if expected_hash and downloaded_hashes.get(relative_path) == expected_hash and local_output_exists:
						continue

					print(f"Syncing output from target: {relative_path}")
					success = network_manager.request_file_from_target(
						target_node.ip,
						target_node.port,
						target_node.auth_token,
						relative_path,
						entry
					)
					if success:
						downloaded_hashes[relative_path] = expected_hash
						download_count += 1
					else:
						print(f"Failed to sync output: {relative_path}")

			if download_count:
				props.sync_status = f"Downloading outputs ({download_count} updated)"
			elif props.monitor_render:
				if props.render_status in ['preparing', 'rendering']:
					props.sync_status = "Rendering..."
				else:
					props.sync_status = "Downloading outputs..."

			if status and props.render_status in ['preparing', 'rendering']:
				return OUTPUT_SYNC_POLL_INTERVAL

			if status and completion_observed_at is None:
				completion_observed_at = now

			if completion_observed_at is None:
				return OUTPUT_SYNC_POLL_INTERVAL

			quiet_reference = max(last_manifest_change, completion_observed_at)
			if now - quiet_reference < OUTPUT_SYNC_QUIET_PERIOD:
				return OUTPUT_SYNC_POLL_INTERVAL

			props.monitor_render = False
			props.sync_status = "Complete"
			return None
		
		timer_manager.register_timer(monitor_progress, interval=1.0, persistent=True)

class REMOTERENDER_OT_CancelRemoteRender(Operator):
	bl_idname = "render_remote.cancel_remote_render"
	bl_label = "Cancel Remote Render"
	bl_description = "Cancel rendering on remote computer"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		if not props.selected_node:
			self.report({'ERROR'}, "No remote node connected")
			return {'CANCELLED'}
		
		# Find connected node
		target_node = None
		for node in context.scene.discovered_nodes:
			if node.node_id == props.selected_node and node.is_connected:
				target_node = node
				break
		
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}
		
		# Send cancel request
		success = network_manager.cancel_remote_render(
			target_node.ip,
			target_node.port,
			target_node.auth_token
		)
		
		if success:
			self.report({'INFO'}, "Render cancelled")
			props.render_status = "cancelled"
			props.sync_status = "Cancelled"
			props.monitor_render = False
		else:
			self.report({'ERROR'}, "Failed to cancel render")
		
		return {'FINISHED'}

class REMOTERENDER_OT_RefreshRenderStatus(Operator):
	bl_idname = "render_remote.refresh_render_status"
	bl_label = "Refresh Status"
	bl_description = "Refresh render status from remote computer"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		
		if not props.selected_node:
			self.report({'ERROR'}, "No remote node connected")
			return {'CANCELLED'}
		
		# Find connected node
		target_node = None
		for node in context.scene.discovered_nodes:
			if node.node_id == props.selected_node and node.is_connected:
				target_node = node
				break
		
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}
		
		# Get render status
		status = network_manager.get_render_status(
			target_node.ip,
			target_node.port,
			target_node.auth_token
		)
		
		if status:
			props.render_status = status.get('status', 'Unknown')
			props.render_progress = status.get('progress', 0.0)
			props.current_frame = status.get('current_frame', 0)
			props.total_frames = status.get('frame_count', 0)
			props.render_elapsed_time = status.get('elapsed_time', 0.0)
			props.render_error_message = sanitize_ui_message(status.get('error_message', ''))
			
			self.report({'INFO'}, f"Status: {format_render_status_label(props.render_status)}")
		else:
			self.report({'ERROR'}, "Failed to get render status")
		
		return {'FINISHED'}

class REMOTERENDER_OT_SelectAllSyncFiles(Operator):
	bl_idname = "render_remote.select_all_sync_files"
	bl_label = "Select All"
	bl_description = "Select all files for synchronization"
	
	def execute(self, context):
		for sync_file in context.scene.sync_files:
			sync_file.selected = True
		return {'FINISHED'}

class REMOTERENDER_OT_DeselectAllSyncFiles(Operator):
	bl_idname = "render_remote.deselect_all_sync_files"
	bl_label = "Deselect All"
	bl_description = "Deselect all files for synchronization"
	
	def execute(self, context):
		for sync_file in context.scene.sync_files:
			sync_file.selected = False
		return {'FINISHED'}

class REMOTERENDER_OT_ClearCache(Operator):
	bl_idname = "render_remote.clear_cache"
	bl_label = "Clear Cache"
	bl_description = "Clear local cache directory"
	
	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		cache_dir = bpy.path.abspath(prefs.remote_cache_directory)
		
		if os.path.exists(cache_dir):
			try:
				shutil.rmtree(cache_dir)
				os.makedirs(cache_dir, exist_ok=True)
				self.report({'INFO'}, "Cache cleared successfully")
			except Exception as e:
				self.report({'ERROR'}, f"Failed to clear cache: {e}")
				return {'CANCELLED'}
		else:
			self.report({'WARNING'}, "Cache directory does not exist")
		
		return {'FINISHED'}

# ----
# UI Panels
# ----

class REMOTERENDER_PT_MainPanel(Panel):
	bl_label = "Remote Render"
	bl_idname = "REMOTERENDER_PT_main_panel"
	bl_description = 'Manage remote rendering options'
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"
	bl_category = "Launch"
	bl_options = {'DEFAULT_CLOSED'}
	bl_order = 64
	
	@classmethod
	def poll(cls, context):
		try:
			return context.preferences.addons[__package__].preferences.remote_enable
		except (AttributeError, KeyError):
			return False
	
	def draw(self, context):
		layout = self.layout
		props = context.scene.remote_render_props
		prefs = context.preferences.addons[__package__].preferences
		
		# Mode Selection
		box = layout.box()
		box.label(text="Operation Mode:", icon='SETTINGS')
		box.prop(props, "mode", expand=True)
		
		layout.separator()
		
		# Dynamic UI based on selected mode
		if props.mode == 'TARGET':
			self.draw_target_mode(layout, props, prefs)
		else:  # SOURCE mode
			self.draw_source_mode(layout, props, prefs)
	
	def draw_target_mode(self, layout, props, prefs):
		"""Draw UI for Target mode"""
		box = layout.box()
		box.label(text="Target Mode:", icon='NETWORK_DRIVE')

		status_box = box.box()
		if network_manager.discovery_active:
			status_box.label(text="Listening for LAN remote render jobs", icon='CHECKMARK')
		else:
			status_box.label(text="Not listening for remote render jobs", icon='PAUSE')
		status_box.label(text="Turning off Render Remote or leaving Target mode stops this service.", icon='INFO')
		
		col = box.column()
		col.prop(props, "node_name")
		
		# Show authentication status from preferences
		if prefs.remote_passcode:
			col.label(text="Authentication: Passcode required", icon='LOCKED')
			col.label(text="Configure passcode in Add-on Preferences.", icon='PREFERENCES')
		else:
			auth_box = box.box()
			auth_box.alert = True
			auth_box.label(text="Set a passcode before allowing remote rendering.", icon='ERROR')
			auth_box.label(text="Configure passcode in Add-on Preferences.", icon='PREFERENCES')
		
		# Discovery controls
		row = box.row(align=True)
		if network_manager.discovery_active:
			row.operator("render_remote.stop_discovery", icon='PAUSE')
			row.label(text="Listening", icon='CHECKMARK')
		else:
			row.operator("render_remote.start_discovery", icon='PLAY')
			row.label(text="Stopped", icon='X')
	
	def draw_source_mode(self, layout, props, prefs):
		"""Draw UI for Source mode"""
		context = bpy.context
		connected_node = get_connected_remote_node(context, props)
		box = layout.box()
		box.label(text="Source Mode:", icon='DESKTOP')
		box.label(text="Source mode uses outbound LAN connections only.", icon='INFO')
		box.label(text="It does not listen for incoming remote render jobs.")
		
		# Network scan
		row = box.row()
		row.operator("render_remote.scan_network", icon='VIEWZOOM')
		
		# Discovered nodes
		if context.scene.discovered_nodes:
			box.label(text="Discovered Nodes:")
			for node in context.scene.discovered_nodes:
				node_box = box.box()
				row = node_box.row()
				
				# Node info
				col = row.column()
				col.label(text=f"{node.name}")
				col.label(text=f"{node.ip}:{node.port}")
				if node.blender_version:
					col.label(text=f"Blender {node.blender_version}")
				
				# Connection status and controls
				col = row.column()
				if node.is_connected:
					col.label(text="Connected", icon='CHECKMARK')
					if node.node_id == props.selected_node:
						col.operator("render_remote.disconnect_node", text="Disconnect")
				else:
					if node.requires_auth:
						col.prop(props, "connection_password", text="Password")
					
					op = col.operator("render_remote.connect_node", text="Connect")
					op.node_id = node.node_id
		
		# Manual connection
		box.separator()
		box.label(text="Manual Connection:")
		col = box.column()
		col.prop(props, "manual_ip")
		col.prop(props, "manual_port")
		col.prop(props, "connection_password", text="Password")
		col.operator("render_remote.connect_manual")
		
		# Selected connection info
		if connected_node:
			box.separator()
			box.label(text=f"Connected Target: {format_connected_remote_label(connected_node)}", icon='LINKED')
		elif props.selected_node:
			box.separator()
			box.label(text="Selected target is no longer connected", icon='ERROR')
		
		# Project settings
		layout.separator()
		box = layout.box()
		box.label(text="Project Settings:", icon='FILE_FOLDER')
		box.prop(props, "project_name")
		
		# Project scanning and sync
		layout.separator()
		self.draw_sync_interface(layout, context, props)
	
	def draw_sync_interface(self, layout, context, props):
		"""Draw file synchronization interface"""
		box = layout.box()
		box.label(text="Input Sync:", icon='FILE_REFRESH')
		
		# Scan button and status
		row = box.row(align=True)
		row.operator("render_remote.scan_project", icon='VIEWZOOM')
		row.label(text=f"Phase: {props.sync_status}", icon='INFO')
		
		# External files warning
		if props.show_external_warning:
			warning_box = box.box()
			warning_box.alert = True
			warning_box.label(text=f"Warning: {props.external_files_count} external files detected!", icon='ERROR')
			warning_box.label(text="External files will NOT be synced to target computer.")
			warning_box.label(text="Only files within the project folder structure are supported.")

		if props.show_missing_warning:
			warning_box = box.box()
			warning_box.alert = True
			warning_box.label(text=f"Warning: {props.missing_files_count} referenced files are missing!", icon='ERROR')
			warning_box.label(text="Missing files must be restored before remote rendering.")
		
		# Sync files list
		if context.scene.sync_files:
			box.label(text="Files to Sync:")
			
			# Select all/none buttons
			row = box.row()
			row.operator("render_remote.select_all_sync_files", text="Select All")
			row.operator("render_remote.deselect_all_sync_files", text="Deselect All")
			
			# File list
			sync_box = box.box()
			for sync_file in context.scene.sync_files:
				row = sync_box.row()
				row.prop(sync_file, "selected", text="")
				
				# File status icon
				if sync_file.status == 'new':
					row.label(text="", icon='FILE_NEW')
				elif sync_file.status == 'modified':
					row.label(text="", icon='FILE_REFRESH')
				elif sync_file.status == 'deleted':
					row.label(text="", icon='X')
				elif sync_file.status in {'external', 'missing'}:
					row.label(text="", icon='ERROR')
				else:
					row.label(text="", icon='FILE')
				
				# File info
				col = row.column()
				col.label(text=sync_file.file_path)
				if sync_file.size > 0:
					size_mb = sync_file.size / (1024 * 1024)
					if size_mb < 1:
						size_str = f"{sync_file.size / 1024:.1f} KB"
					else:
						size_str = f"{size_mb:.1f} MB"
					col.label(text=f"{sync_file.status.upper()} - {size_str}")
				else:
					col.label(text=sync_file.status.upper())
			
			# Sync button
			box.operator("render_remote.sync_files", icon='FILE_REFRESH')
		
		elif props.sync_status == "Up to date":
			box.label(text="All files are synchronized!", icon='CHECKMARK')
		
		# Render Management Interface
		if get_connected_remote_node(context, props):
			layout.separator()
			self.draw_render_interface(layout, context, props)
	
	def draw_render_interface(self, layout, context, props):
		"""Draw render management interface"""
		box = layout.box()
		box.label(text="Remote Rendering:", icon='RENDER_ANIMATION')
		active_workflow = props.monitor_render or props.render_status in ['preparing', 'rendering']
		
		# Render controls
		if active_workflow:
			row = box.row(align=True)
			if props.render_status in ['preparing', 'rendering']:
				row.operator("render_remote.cancel_remote_render", icon='X')
			row.operator("render_remote.refresh_render_status", icon='FILE_REFRESH')

			progress_box = box.box()
			progress_box.label(text=f"Phase: {props.sync_status}", icon='INFO')
			progress_box.label(text=f"Render Status: {format_render_status_label(props.render_status)}", icon='RENDER_ANIMATION')

			if props.render_progress > 0:
				row = progress_box.row()
				row.label(text=f"Progress: {props.render_progress:.1f}%")

			if props.total_frames > 1:
				row = progress_box.row()
				row.label(text=f"Frame: {props.current_frame} / {props.total_frames}")

			if props.render_elapsed_time > 0:
				elapsed_minutes = int(props.render_elapsed_time // 60)
				elapsed_seconds = int(props.render_elapsed_time % 60)
				row = progress_box.row()
				row.label(text=f"Elapsed: {elapsed_minutes:02d}:{elapsed_seconds:02d}")

			if props.monitor_render and props.render_status not in ['preparing', 'rendering']:
				progress_box.label(text="Waiting for output sync to settle.", icon='INFO')

			if props.render_error_message:
				error_box = progress_box.box()
				error_box.alert = True
				error_box.label(text=f"Error: {props.render_error_message}", icon='ERROR')
		else:
			if props.show_external_warning or props.show_missing_warning:
				warning_box = box.box()
				warning_box.alert = True
				warning_box.label(text="Resolve missing or unsupported references before rendering.", icon='ERROR')

			row = box.row()
			row.enabled = not (props.show_external_warning or props.show_missing_warning)
			op = row.operator("render_remote.start_remote_render", text="Render Animation", icon='RENDER_ANIMATION')
			op.animation = True
			
			# Status refresh
			row = box.row()
			row.operator("render_remote.refresh_render_status", icon='FILE_REFRESH')
			
			# Show last status if available
			if props.render_status and props.render_status != "Not Started":
				status_box = box.box()
				status_box.label(text=f"Last Status: {format_render_status_label(props.render_status)}")
				if props.sync_status not in {"Not Scanned", "Up to date"}:
					status_box.label(text=f"Last Phase: {props.sync_status}", icon='INFO')
				
				if props.render_error_message:
					status_box.label(text=f"Error: {props.render_error_message}", icon='ERROR')
		
		# Output monitoring info
		box.separator()
		box.label(text="Output File Sync:", icon='FILE_REFRESH')
		box.label(text="Outputs are pulled back into this project automatically")
		box.label(text="Relative folder structure is preserved")
		box.label(text="Source mode initiates the transfer connections")
		
		# Show project root info
		if bpy.data.filepath:
			project_root = file_sync_manager.get_project_root()
			if project_root:
				box.label(text=f"Project root: {os.path.basename(project_root)}/", icon='FILE_FOLDER')
		else:
			box.label(text="(Save your project to see sync info)", icon='INFO')

# ----
# Render Handler Functions (Module Level)
# ----

@persistent
def _render_pre_handler(scene, depsgraph):
	"""Called before rendering starts"""
	global render_manager
	render_manager.render_status = "rendering"
	render_manager.current_frame = scene.frame_current
	print(f"Render started for frame {render_manager.current_frame}")

@persistent
def _render_post_handler(scene, depsgraph):
	"""Called after rendering completes"""
	global render_manager
	if render_manager.render_status != "cancelled":
		render_manager.render_status = "completed"
	print(f"Render completed for frame {render_manager.current_frame}")

@persistent
def _render_cancel_handler(scene, depsgraph):
	"""Called when render is cancelled"""
	global render_manager, network_manager
	render_manager.render_status = "cancelled"
	render_manager.active_render = False
	
	# Stop output file monitoring immediately on cancel
	if render_manager.output_file_monitor:
		render_manager.output_file_monitor.stop_monitoring()
		render_manager.output_file_monitor = None
	
	render_manager._clear_render_handlers()
	
	# Mark rendering as complete so connections can be cleaned up if needed
	network_manager.is_rendering = False
	print("Render cancelled")

@persistent
def _render_complete_handler(scene, depsgraph):
	"""Called when all rendering is complete"""
	global render_manager, network_manager
	render_manager.render_status = "completed"
	render_manager.render_progress = 100.0
	render_manager.active_render = False
	
	# Trigger post-processing file monitoring
	if render_manager.output_file_monitor:
		render_manager.output_file_monitor.on_render_complete(scene, depsgraph)
	
	# Clean up after a delay to allow final file operations
	def delayed_cleanup():
		time.sleep(10)  # Wait 10 seconds for final file operations
		if render_manager.output_file_monitor:
			render_manager.output_file_monitor.stop_monitoring()
			render_manager.output_file_monitor = None
		render_manager._clear_render_handlers()
		network_manager.is_rendering = False
		print("Render cleanup completed")
	
	threading.Thread(target=delayed_cleanup, daemon=True).start()
	
	print("All rendering completed")

@persistent
def _render_write_handler(scene, depsgraph):
	"""Called when frame is written to disk"""
	global render_manager
	
	# Trigger immediate file detection
	if render_manager.output_file_monitor:
		render_manager.output_file_monitor.on_frame_written(scene, depsgraph)
	
	# Update progress
	if render_manager.frame_count > 0:
		frames_completed = (render_manager.current_frame - scene.frame_start + 1)
		render_manager.render_progress = (frames_completed / render_manager.frame_count) * 100.0
	
	print(f"Frame {render_manager.current_frame} written to disk, progress: {render_manager.render_progress:.1f}%")

# ----
# Cleanup Functions
# ----

@persistent
def cleanup_on_exit(dummy):
	"""Clean up network connections on Blender exit"""
	shutdown(force=False)

@persistent
def cleanup_on_load_pre(dummy):
	"""Clean up before loading files"""
	global render_manager, timer_manager, network_manager

	if network_manager and network_manager.is_rendering:
		print("Skipping load cleanup - remote render is preparing a file")
		return
	
	try:
		# Only cleanup render manager if not actively rendering
		if render_manager and not render_manager.active_render:
			render_manager.cleanup()
	except Exception as e:
		print(f"Error cleaning up render manager on load: {e}")
	
	try:
		# Clean up any stale timers
		if timer_manager:
			timer_manager.cleanup_all()
	except Exception as e:
		print(f"Error cleaning up timers on load: {e}")

@persistent
def reset_connection_status_on_load(dummy):
	"""Reset connection status when loading new projects"""
	if network_manager and network_manager.is_rendering:
		return
	try:
		# Clear previous connection data when opening a new project
		context = bpy.context
		if hasattr(context.scene, 'remote_render_props'):
			props = context.scene.remote_render_props
			
			# Reset connection status but keep discovered nodes
			if hasattr(context.scene, 'discovered_nodes'):
				for node in context.scene.discovered_nodes:
					node.is_connected = False
					node.auth_token = ""
			
			# Reset selection and status
			props.selected_node = ""
			props.sync_status = "Not Scanned"
			props.render_status = "Not Started"
			props.monitor_render = False

			# Clear sync files
			if hasattr(context.scene, 'sync_files'):
				context.scene.sync_files.clear()

			if network_manager:
				network_manager.revoke_auth_sessions()
	except Exception as e:
		print(f"Error resetting connection status: {e}")

# ----
# Registration
# ----

_is_registered = False

classes = (
	SyncFileInfo,
	RemoteNodeProperties,
	RemoteRenderProperties,
	REMOTERENDER_OT_StartDiscovery,
	REMOTERENDER_OT_StopDiscovery,
	REMOTERENDER_OT_ScanNetwork,
	REMOTERENDER_OT_ConnectNode,
	REMOTERENDER_OT_DisconnectNode,
	REMOTERENDER_OT_ConnectManual,
	REMOTERENDER_OT_ScanProject,
	REMOTERENDER_OT_SyncFiles,
	REMOTERENDER_OT_SelectAllSyncFiles,
	REMOTERENDER_OT_DeselectAllSyncFiles,
	REMOTERENDER_OT_ClearCache,
	REMOTERENDER_OT_StartRemoteRender,
	REMOTERENDER_OT_CancelRemoteRender,
	REMOTERENDER_OT_RefreshRenderStatus,
	REMOTERENDER_PT_MainPanel,
)

def shutdown(force=False):
	"""Stop all Render Remote runtime activity."""
	global network_manager, render_manager, timer_manager

	rendering_active = bool(
		(network_manager and network_manager.is_rendering) or
		(render_manager and render_manager.active_render)
	)
	if rendering_active and not force:
		print("Skipping remote render shutdown - rendering in progress")
		return

	print("Cleaning up remote render resources")

	try:
		if render_manager:
			render_manager.cleanup()
	except Exception as e:
		print(f"Error cleaning up render manager: {e}")

	try:
		if network_manager:
			network_manager.shutdown(force=force)
	except Exception as e:
		print(f"Error stopping network manager: {e}")

	try:
		if timer_manager:
			timer_manager.cleanup_all()
	except Exception as e:
		print(f"Error cleaning up timer manager: {e}")

def register():
	global _is_registered

	if _is_registered:
		return

	# Register all classes
	for cls in classes:
		try:
			bpy.utils.register_class(cls)
		except (RuntimeError, ValueError) as e:
			if "already registered" not in str(e):
				raise
		
	# Register property groups
	if not hasattr(bpy.types.Scene, 'remote_render_props'):
		bpy.types.Scene.remote_render_props = bpy.props.PointerProperty(type=RemoteRenderProperties)
	if not hasattr(bpy.types.Scene, 'discovered_nodes'):
		bpy.types.Scene.discovered_nodes = bpy.props.CollectionProperty(type=RemoteNodeProperties)
	if not hasattr(bpy.types.Scene, 'sync_files'):
		bpy.types.Scene.sync_files = bpy.props.CollectionProperty(type=SyncFileInfo)
	
	# Register cleanup handlers
	if cleanup_on_exit not in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.append(cleanup_on_exit)
	
	if cleanup_on_load_pre not in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.append(cleanup_on_load_pre)
	
	if reset_connection_status_on_load not in bpy.app.handlers.load_post:
		bpy.app.handlers.load_post.append(reset_connection_status_on_load)

	atexit.register(shutdown)

	_is_registered = True
	print("Remote Render Sync add-on registered")

def unregister():
	global _is_registered

	if render_manager:
		render_manager._clear_render_handlers()

	shutdown(force=True)

	# Remove handlers
	if cleanup_on_exit in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.remove(cleanup_on_exit)
		
	if cleanup_on_load_pre in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.remove(cleanup_on_load_pre)
	
	if reset_connection_status_on_load in bpy.app.handlers.load_post:
		bpy.app.handlers.load_post.remove(reset_connection_status_on_load)
		
	# Unregister classes
	for cls in reversed(classes):
		try:
			bpy.utils.unregister_class(cls)
		except RuntimeError:
			pass
		except ValueError:
			pass
		
	# Remove properties
	if hasattr(bpy.types.Scene, 'remote_render_props'):
		del bpy.types.Scene.remote_render_props
	if hasattr(bpy.types.Scene, 'discovered_nodes'):
		del bpy.types.Scene.discovered_nodes
	if hasattr(bpy.types.Scene, 'sync_files'):
		del bpy.types.Scene.sync_files
	
	_is_registered = False
	print("Remote Render Sync add-on unregistered")
