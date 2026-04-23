import bpy
import threading
import time
from bpy.app.handlers import persistent
from .timers import timer_manager

@persistent
def _render_pre_handler(scene, depsgraph):
	"""Called before rendering starts"""
	from .render import render_manager
	render_manager.render_status = "rendering"
	render_manager.current_frame = scene.frame_current
	print(f"Render started for frame {render_manager.current_frame}")

@persistent
def _render_post_handler(scene, depsgraph):
	"""Called after rendering completes"""
	from .render import render_manager
	if render_manager.render_status != "cancelled":
		render_manager.render_status = "completed"
	print(f"Render completed for frame {render_manager.current_frame}")

@persistent
def _render_cancel_handler(scene, depsgraph):
	"""Called when render is cancelled"""
	from .render import render_manager
	from .network import network_manager
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
	from .render import render_manager
	from .network import network_manager
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
	from .render import render_manager

	# Trigger immediate file detection
	if render_manager.output_file_monitor:
		render_manager.output_file_monitor.on_frame_written(scene, depsgraph)

	# Update progress
	if render_manager.frame_count > 0:
		frames_completed = (render_manager.current_frame - scene.frame_start + 1)
		render_manager.render_progress = (frames_completed / render_manager.frame_count) * 100.0

	print(f"Frame {render_manager.current_frame} written to disk, progress: {render_manager.render_progress:.1f}%")

@persistent
def cleanup_on_exit(dummy):
	"""Clean up network connections on Blender exit"""
	from .network import network_manager
	from .render import render_manager
	shutdown(force=False)

@persistent
def cleanup_on_load_pre(dummy):
	"""Clean up before loading files"""
	from .network import network_manager
	from .render import render_manager

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
	from .network import network_manager
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

def shutdown(force=False):
	"""Stop all Render Remote runtime activity."""
	from .network import network_manager
	from .render import render_manager

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
