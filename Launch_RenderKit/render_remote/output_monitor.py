import bpy
import os
import re
import threading
import time
from pathlib import Path
from .constants import OUTPUT_SYNC_POLL_INTERVAL, OUTPUT_SYNC_POST_PROCESS_TIMEOUT
from .paths import (FileFilter, PathSecurityError, normalize_relative_path,
                    resolve_under_root, relative_path_under_root,
                    is_reserved_input_manifest_path)
from .file_sync import file_sync_manager

class OutputFileMonitor:
	"""Monitors target-side render outputs and exposes them as a manifest"""

	def __init__(self, project_root, source_project_root):
		self.project_root = str(Path(project_root).expanduser().resolve(strict=False))
		self.source_project_root = None
		if source_project_root:
			self.source_project_root = str(Path(source_project_root).expanduser().resolve(strict=False))
		self.blend_file_path = bpy.data.filepath
		self.monitoring = False
		self.monitor_thread = None
		self._stop_event = threading.Event()
		self.scan_lock = threading.Lock()
		self.manifest_lock = threading.Lock()
		self.output_roots = set()
		self.known_files = {}
		self.output_manifest = {}
		self.sidecar_paths = set(file_sync_manager.get_renderkit_sidecar_candidates(self.blend_file_path))
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

		if os.path.isdir(normalized_path):
			return normalized_path

		if os.path.splitext(basename)[1]:
			return os.path.dirname(normalized_path)

		if basename.lower() in FileFilter.RENDER_OUTPUT_PATTERNS:
			return normalized_path

		if basename.endswith(('_', '-')) or '#' in basename:
			return os.path.dirname(normalized_path)

		return os.path.dirname(normalized_path) or normalized_path

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
		if scene.render.filepath:
			main_output_path = self._resolve_output_path_under_workspace(
				bpy.path.abspath(scene.render.filepath),
				"renders"
			)
			scene.render.filepath = main_output_path

		for index, node in enumerate(self._iter_output_file_nodes(scene) or []):
			path_attr = 'directory' if hasattr(node, 'directory') else 'base_path'
			configured_directory = getattr(node, path_attr, '')
			fallback_relative = f"renders/compositor/{self._make_safe_segment(node.name, f'node-{index + 1}')}"
			target_directory = self._resolve_output_path_under_workspace(
				bpy.path.abspath(configured_directory) if configured_directory else '',
				fallback_relative
			)
			setattr(node, path_attr, target_directory)

		self.output_roots = {self.project_root} if self._is_within_workspace(self.project_root) else set()
		print(f"Configured output roots: {sorted(self.output_roots)}")

	def _add_output_root(self, output_root):
		"""Add a monitored output directory when it stays inside the target workspace."""
		normalized_root = self._normalize_existing_path(output_root)
		if normalized_root and self._is_within_workspace(normalized_root):
			self.output_roots.add(normalized_root)

	def _refresh_renderkit_output_roots(self, scene):
		"""Retained for handler compatibility; the whole project cache is monitored."""
		self._add_output_root(self.project_root)

	def _iter_sidecar_files(self):
		"""Yield RenderKit sidecar files such as the total render time log."""
		for file_path in sorted(self.sidecar_paths):
			if (
				os.path.isfile(file_path)
				and self._is_within_workspace(file_path)
				and not FileFilter.should_ignore_file(file_path)
			):
				yield file_path

	def _iter_output_files(self):
		"""Yield current files from known output roots"""
		seen_paths = set()
		for file_path in self._iter_sidecar_files():
			if file_path not in seen_paths:
				seen_paths.add(file_path)
				yield file_path

		for output_root in sorted(self.output_roots):
			if not os.path.exists(output_root):
				continue

			for root, dirs, files in os.walk(output_root):
				for file_name in files:
					file_path = os.path.join(root, file_name)
					if file_path in seen_paths:
						continue
					if not self._is_within_workspace(file_path):
						continue
					try:
						relative_path = relative_path_under_root(file_path, self.project_root)
						if is_reserved_input_manifest_path(relative_path):
							continue
					except PathSecurityError:
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
		self._refresh_renderkit_output_roots(scene)

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
		self._refresh_renderkit_output_roots(scene)
		self._scan_for_new_files()

		def post_processing_monitor():
			monitor_time = 0
			while monitor_time < OUTPUT_SYNC_POST_PROCESS_TIMEOUT:
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
