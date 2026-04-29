import bpy
import os
import re
import subprocess
import threading
import time
from .timers import timer_manager
from .output_monitor import OutputFileMonitor
from .constants import OUTPUT_SYNC_POST_PROCESS_TIMEOUT, RENDER_SAMPLE_PROGRESS_THRESHOLD
from .paths import resolve_under_root

_RE_FRAME     = re.compile(r'Fra:(\d+)')
_RE_SAMPLES   = re.compile(r'Rendering\s+(\d+)\s*/\s*(\d+)\s+samples')
_RE_SAVED     = re.compile(r'\bSaved\b', re.IGNORECASE)

class RenderManager:
	"""Manages rendering operations on target computers"""

	def __init__(self):
		self.active_render = None
		self.render_thread = None
		self.render_progress = 0.0
		self.render_status = "idle"
		self.render_start_time = None
		self.render_elapsed_time = None
		self.render_estimated_time = None
		self.render_error_message = ""
		self.frame_count = 0
		self.current_frame = 0
		self.output_paths = []
		self.original_output_path = ""
		self.render_queue = []
		self.output_file_monitor = None
		self.render_process = None
		self._render_process_lock = threading.Lock()
		self._cancel_requested = False

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

		if not os.path.exists(blend_file_path):
			raise Exception(f'Blend file not found: {blend_file_path}')

		self.render_status = "preparing"
		self.render_start_time = time.time()
		self.render_elapsed_time = None
		self.render_estimated_time = None
		self.render_progress = 0.0
		self.render_error_message = ""
		self.active_render = True
		self._cancel_requested = False
		if render_settings.get('animation', False):
			self.frame_count = render_settings.get('frame_end', 0) - render_settings.get('frame_start', 0) + 1
		else:
			self.frame_count = 1
		self.current_frame = render_settings.get('frame_current', render_settings.get('frame_start', 1))

		self._setup_output_file_monitoring(source_project_root, blend_file_path=blend_file_path)
		self._start_background_render(blend_file_path, render_settings)

	def _build_background_render_command(self, blender_binary, blend_file_path, render_settings):
		"""Build a Blender command that renders the cached blend file directly."""
		command = [
			blender_binary,
			'--background',
			blend_file_path,
		]

		if render_settings.get('output_relative_path'):
			project_root = os.path.dirname(os.path.dirname(blend_file_path))
			output_path = resolve_under_root(project_root, render_settings['output_relative_path'])
			command.extend(['--render-output', output_path])

		if render_settings.get('animation', False):
			command.append('--render-anim')
		else:
			frame = int(render_settings.get('frame_current', render_settings.get('frame_start', 1)))
			command.extend(['--render-frame', str(frame)])

		return command

	def _start_background_render(self, blend_file_path, render_settings):
		"""Start a cancellable background Blender render process."""
		from .network import network_manager
		blender_binary = getattr(bpy.app, 'binary_path', None)
		if not blender_binary:
			raise Exception("Blender executable path is unavailable")

		command = self._build_background_render_command(blender_binary, blend_file_path, render_settings)
		print(f"Starting background remote render: {blend_file_path}")
		process = subprocess.Popen(
			command,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True
		)

		with self._render_process_lock:
			self.render_process = process
			self.render_status = "rendering"
			network_manager.is_rendering = True

		self.render_thread = threading.Thread(
			target=self._watch_background_render,
			args=(process, render_settings),
			daemon=True
		)
		self.render_thread.start()

	def _update_progress(self, progress, now):
		"""Write render_progress and recompute render_estimated_time from coherent data."""
		self.render_progress = progress
		if self.render_start_time and progress > 0:
			elapsed = now - self.render_start_time
			if elapsed > 0:
				self.render_estimated_time = elapsed * (100.0 - progress) / progress
		else:
			self.render_estimated_time = None

	def _watch_background_render(self, process, render_settings):
		"""Stream stdout from the child Blender process and publish live render state."""
		from .network import network_manager

		is_animation = render_settings.get('animation', False)
		frame_start  = render_settings.get('frame_start', 1)
		frame_count  = self.frame_count

		frames_completed = 0
		current_frame_number = self.current_frame
		frame_start_time = time.time()
		last_line = ""

		try:
			for raw_line in process.stdout:
				with self._render_process_lock:
					if self.render_process is not process:
						break

				line = raw_line.rstrip()
				if line:
					last_line = line

				m_frame = _RE_FRAME.search(line)
				if m_frame:
					new_frame = int(m_frame.group(1))
					if new_frame != current_frame_number:
						current_frame_number = new_frame
						self.current_frame = new_frame
						frame_start_time = time.time()

				m_saved = _RE_SAVED.search(line)
				if m_saved:
					frames_completed += 1
					now = time.time()
					if frame_count > 0:
						self._update_progress((frames_completed / frame_count) * 100.0, now)
					frame_start_time = now

				m_samples = _RE_SAMPLES.search(line)
				if m_samples and not m_saved:
					elapsed = time.time() - frame_start_time
					if elapsed >= RENDER_SAMPLE_PROGRESS_THRESHOLD:
						done_samples  = int(m_samples.group(1))
						total_samples = int(m_samples.group(2))
						if total_samples > 0 and frame_count > 0:
							frame_base = frames_completed / frame_count
							frame_share = 1.0 / frame_count
							sample_frac = done_samples / total_samples
							self._update_progress((frame_base + frame_share * sample_frac) * 100.0, time.time())

		except Exception as e:
			last_line = str(e)

		return_code = -1
		try:
			return_code = process.wait()
		except Exception:
			pass

		with self._render_process_lock:
			if self.render_process is not process:
				return
			cancelled = self._cancel_requested
			self.render_process = None

		if cancelled or return_code < 0:
			self.render_status = "cancelled"
			self.render_error_message = ""
			if self.output_file_monitor:
				self.output_file_monitor.stop_monitoring()
				self.output_file_monitor = None
		elif return_code == 0:
			self.render_status = "completed"
			self.render_progress = 100.0
			if self.render_start_time:
				self.render_elapsed_time = time.time() - self.render_start_time
			if self.output_file_monitor:
				time.sleep(OUTPUT_SYNC_POST_PROCESS_TIMEOUT)
				self.output_file_monitor.stop_monitoring()
		else:
			self.render_status = "error"
			lines = [l for l in last_line.splitlines() if l.strip()]
			self.render_error_message = lines[-1][-500:] if lines else f"Blender exited with code {return_code}"
			if self.output_file_monitor:
				self.output_file_monitor.stop_monitoring()

		self.active_render = False
		network_manager.is_rendering = False
		print(f"Background remote render finished with status: {self.render_status}")

	def _setup_output_file_monitoring(self, source_project_root, blend_file_path=None):
		"""Set up monitoring for newly created files during rendering"""
		blend_file_path = blend_file_path or bpy.data.filepath
		if not blend_file_path:
			return

		project_root = os.path.dirname(os.path.dirname(blend_file_path))

		self.output_file_monitor = OutputFileMonitor(
			project_root,
			source_project_root,
			blend_file_path=blend_file_path,
			configure_scene=False
		)
		self.output_file_monitor.start_monitoring()

	def cancel_render(self):
		"""Cancel active render"""
		from .network import network_manager
		self._cancel_requested = True
		self.render_queue.clear()

		with self._render_process_lock:
			process = self.render_process

		if process and process.poll() is None:
			try:
				process.terminate()
				process.wait(timeout=5)
			except subprocess.TimeoutExpired:
				process.kill()
			except Exception as e:
				print(f"Background render termination failed: {e}")

		if self.output_file_monitor:
			self.output_file_monitor.stop_monitoring()
			self.output_file_monitor = None

		self.render_status = "cancelled"
		self.active_render = False
		network_manager.is_rendering = False

	def get_render_status(self):
		"""Get current render status"""
		if self.render_elapsed_time is not None:
			elapsed_time = self.render_elapsed_time
		elif self.render_start_time:
			elapsed_time = time.time() - self.render_start_time
		else:
			elapsed_time = 0.0

		return {
			'status': self.render_status,
			'progress': self.render_progress,
			'elapsed_time': elapsed_time,
			'estimated_time': self.render_estimated_time,
			'error_message': self.render_error_message
		}

	def cleanup(self):
		"""Clean up render manager resources"""
		from .network import network_manager
		self.render_queue.clear()
		self.active_render = False

		if self.output_file_monitor:
			self.output_file_monitor.stop_monitoring()
			self.output_file_monitor = None

		network_manager.is_rendering = False

# Global render manager singleton
render_manager = RenderManager()
