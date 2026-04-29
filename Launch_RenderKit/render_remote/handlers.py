import bpy
from bpy.app.handlers import persistent
from .timers import timer_manager

@persistent
def cleanup_on_exit(dummy):
	"""Clean up network connections on Blender exit"""
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
		if render_manager and not render_manager.active_render:
			render_manager.cleanup()
	except Exception as e:
		print(f"Error cleaning up render manager on load: {e}")

	try:
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
		context = bpy.context

		if hasattr(context.window_manager, 'remote_render_discovered_nodes'):
			for node in context.window_manager.remote_render_discovered_nodes:
				node.is_connected = False
				node.auth_token = ""

		if hasattr(context.window_manager, 'remote_render_state'):
			state = context.window_manager.remote_render_state
			state.remote_sync_status = "Not Scanned"
			state.remote_render_status = "Not Started"
			state.remote_monitor_render = False
			from .ui import initialize_remote_runtime_state
			initialize_remote_runtime_state(context)

		if hasattr(context.window_manager, 'remote_render_sync_files'):
			context.window_manager.remote_render_sync_files.clear()

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
