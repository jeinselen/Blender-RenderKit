import bpy
import os
import threading
import time
from pathlib import Path
from .timers import timer_manager
from .output_monitor import OutputFileMonitor

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
			from .network import network_manager
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
		from .network import network_manager
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
		from .paths import resolve_under_root
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
			from .handlers import (
				_render_pre_handler, _render_post_handler, _render_cancel_handler,
				_render_complete_handler, _render_write_handler
			)
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

		from .handlers import (  # noqa: PLC0415 — late import avoids circular dependency
			_render_pre_handler, _render_post_handler, _render_cancel_handler,
			_render_complete_handler, _render_write_handler
		)
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
			from .network import network_manager
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
			from .network import network_manager
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
				from .network import network_manager
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
		from .network import network_manager
		self.render_queue.clear()
		self.active_render = False

		# Stop output file monitoring
		if self.output_file_monitor:
			self.output_file_monitor.stop_monitoring()
			self.output_file_monitor = None

		self._clear_render_handlers()

		# Reset rendering flag
		network_manager.is_rendering = False

# Global render manager singleton
render_manager = RenderManager()
