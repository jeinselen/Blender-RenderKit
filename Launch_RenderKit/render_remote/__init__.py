import atexit
import bpy
from .timers import timer_manager
from .file_sync import file_sync_manager
from .network import network_manager, NetworkManager
from .render import render_manager, RenderManager
from .constants import (ADDON_PACKAGE, AUTH_MAX_CHALLENGES, AUTH_PBKDF2_ITERATIONS,
                        INPUT_MANIFEST_FILENAME, PROTOCOL_MAX_MESSAGE_SIZE,
                        addon_package_from_module_package,
                        default_remote_cache_directory, is_allowed_lan_ip)
from .paths import (FileFilter, PathSecurityError, normalize_relative_path,
                    relative_path_under_root, resolve_under_root)
from .protocol import (ProtocolError, error_response, recv_file, recv_message,
                       send_file, send_message, validate_message)
from .handlers import (cleanup_on_exit, cleanup_on_load_pre, reset_connection_status_on_load,
                       shutdown)
from .ui import (SyncFileInfo, RemoteNodeProperties, RemoteRuntimeState,
                 initialize_remote_runtime_state,
                 REMOTERENDER_OT_StartDiscovery, REMOTERENDER_OT_StopDiscovery,
                 REMOTERENDER_OT_ScanNetwork, REMOTERENDER_OT_ConnectNode,
                 REMOTERENDER_OT_DisconnectNode, REMOTERENDER_OT_ConnectManual,
                 REMOTERENDER_OT_ScanProject, REMOTERENDER_OT_SyncFiles,
                 REMOTERENDER_OT_SelectAllSyncFiles, REMOTERENDER_OT_DeselectAllSyncFiles,
                 REMOTERENDER_OT_ClearCache, REMOTERENDER_OT_StartRemoteRender,
                 REMOTERENDER_OT_CancelRemoteRender, REMOTERENDER_OT_RefreshRenderStatus,
                 REMOTERENDER_PT_MainPanel)

_is_registered = False
_atexit_registered = False

classes = (
	SyncFileInfo,
	RemoteNodeProperties,
	RemoteRuntimeState,
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

def register():
	global _is_registered, _atexit_registered

	if _is_registered:
		return

	try:
		REMOTERENDER_PT_MainPanel.bl_category = bpy.context.preferences.addons[ADDON_PACKAGE].preferences.remote_category
	except (AttributeError, KeyError):
		pass

	# Register all classes
	for cls in classes:
		if cls is REMOTERENDER_PT_MainPanel and not REMOTERENDER_PT_MainPanel.bl_category:
			continue
		try:
			bpy.utils.register_class(cls)
		except (RuntimeError, ValueError) as e:
			if "already registered" not in str(e):
				raise

	# Register transient runtime collections on WindowManager so Render Remote does not dirty .blend files.
	if not hasattr(bpy.types.WindowManager, 'remote_render_discovered_nodes'):
		bpy.types.WindowManager.remote_render_discovered_nodes = bpy.props.CollectionProperty(type=RemoteNodeProperties)
	if not hasattr(bpy.types.WindowManager, 'remote_render_sync_files'):
		bpy.types.WindowManager.remote_render_sync_files = bpy.props.CollectionProperty(type=SyncFileInfo)
	if not hasattr(bpy.types.WindowManager, 'remote_render_state'):
		bpy.types.WindowManager.remote_render_state = bpy.props.PointerProperty(type=RemoteRuntimeState)
	initialize_remote_runtime_state(bpy.context)

	# Register cleanup handlers
	if cleanup_on_exit not in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.append(cleanup_on_exit)

	if cleanup_on_load_pre not in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.append(cleanup_on_load_pre)

	if reset_connection_status_on_load not in bpy.app.handlers.load_post:
		bpy.app.handlers.load_post.append(reset_connection_status_on_load)

	if not _atexit_registered:
		atexit.register(shutdown)
		_atexit_registered = True

	_is_registered = True
	print("Remote Render Sync add-on registered")

def unregister():
	global _is_registered, _atexit_registered

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

	if _atexit_registered:
		try:
			atexit.unregister(shutdown)
		except (AttributeError, ValueError):
			pass
		_atexit_registered = False

	# Unregister classes
	for cls in reversed(classes):
		try:
			bpy.utils.unregister_class(cls)
		except RuntimeError:
			pass
		except ValueError:
			pass

	# Remove transient runtime collections
	if hasattr(bpy.types.WindowManager, 'remote_render_discovered_nodes'):
		del bpy.types.WindowManager.remote_render_discovered_nodes
	if hasattr(bpy.types.WindowManager, 'remote_render_sync_files'):
		del bpy.types.WindowManager.remote_render_sync_files
	if hasattr(bpy.types.WindowManager, 'remote_render_state'):
		del bpy.types.WindowManager.remote_render_state

	_is_registered = False
	print("Remote Render Sync add-on unregistered")

def is_registered():
	"""Return whether Render Remote UI/runtime hooks are registered."""
	return _is_registered

def set_panel_category(category):
	"""Update the 3D View panel category without registering Render Remote when disabled."""
	REMOTERENDER_PT_MainPanel.bl_category = category
	if not _is_registered:
		return

	try:
		bpy.utils.unregister_class(REMOTERENDER_PT_MainPanel)
	except (RuntimeError, ValueError):
		pass

	if len(category) > 0:
		bpy.utils.register_class(REMOTERENDER_PT_MainPanel)

__all__ = (
	"AUTH_MAX_CHALLENGES",
	"AUTH_PBKDF2_ITERATIONS",
	"FileFilter",
	"INPUT_MANIFEST_FILENAME",
	"NetworkManager",
	"PathSecurityError",
	"PROTOCOL_MAX_MESSAGE_SIZE",
	"ProtocolError",
	"REMOTERENDER_PT_MainPanel",
	"RenderManager",
	"error_response",
	"file_sync_manager",
	"is_allowed_lan_ip",
	"is_registered",
	"addon_package_from_module_package",
	"default_remote_cache_directory",
	"network_manager",
	"normalize_relative_path",
	"recv_file",
	"recv_message",
	"register",
	"relative_path_under_root",
	"render_manager",
	"resolve_under_root",
	"send_file",
	"send_message",
	"set_panel_category",
	"shutdown",
	"timer_manager",
	"unregister",
	"validate_message",
)
