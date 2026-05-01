import bpy
import os
import re
import shutil
import threading
import time
from types import SimpleNamespace
from bpy.props import StringProperty, BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator, Panel, PropertyGroup
from .constants import (ADDON_PACKAGE, build_source_project_cache_name, OUTPUT_SYNC_POLL_INTERVAL, OUTPUT_SYNC_QUIET_PERIOD, OUTPUT_SYNC_POST_PROCESS_TIMEOUT)
from .paths import PathSecurityError, normalize_relative_path, resolve_under_root, relative_path_under_root
from .protocol import error_response
from .file_sync import file_sync_manager
from .local_state import (default_remote_node_name, get_local_lan_ip, get_local_remote_mode, set_local_remote_mode)
from .network import network_manager
from .render import render_manager
from .timers import timer_manager

_SYNCING_LOCAL_REMOTE_STATE = False
_REMOTE_WORKFLOW_LOCK = threading.Lock()
_REMOTE_WORKFLOW_ID = 0
_REMOTE_WORKFLOW_CANCEL_EVENT = threading.Event()

class RemoteWorkflowCancelled(Exception):
	"""Raised when the source-side remote render workflow is cancelled."""
	pass

def begin_remote_workflow():
	"""Create a cancellation token for the active source-side workflow."""
	global _REMOTE_WORKFLOW_ID, _REMOTE_WORKFLOW_CANCEL_EVENT
	with _REMOTE_WORKFLOW_LOCK:
		_REMOTE_WORKFLOW_CANCEL_EVENT.set()
		_REMOTE_WORKFLOW_ID += 1
		_REMOTE_WORKFLOW_CANCEL_EVENT = threading.Event()
		return _REMOTE_WORKFLOW_ID, _REMOTE_WORKFLOW_CANCEL_EVENT

def cancel_remote_workflows():
	"""Signal all active source-side remote render work to stop."""
	with _REMOTE_WORKFLOW_LOCK:
		_REMOTE_WORKFLOW_CANCEL_EVENT.set()

def is_current_remote_workflow(workflow_id):
	with _REMOTE_WORKFLOW_LOCK:
		return workflow_id == _REMOTE_WORKFLOW_ID

def raise_if_workflow_cancelled(cancel_event):
	if cancel_event and cancel_event.is_set():
		raise RemoteWorkflowCancelled()

def get_remote_props(context):
	"""Return transient Render Remote workflow state."""
	return context.window_manager.remote_render_state

def get_remote_preferences(context):
	"""Return persistent Render Remote preferences."""
	return context.preferences.addons[ADDON_PACKAGE].preferences

def get_discovered_nodes(context):
	"""Return transient discovered-node storage that is not saved into blend files."""
	return context.window_manager.remote_render_discovered_nodes

def get_sync_files(context):
	"""Return transient sync-file storage that is not saved into blend files."""
	return context.window_manager.remote_render_sync_files

def get_remote_mode(context):
	"""Return the host-local Render Remote operation mode."""
	return get_remote_props(context).remote_mode

def get_remote_node_name():
	"""Return the local machine name used for target discovery."""
	return default_remote_node_name()

def initialize_remote_runtime_state(context):
	"""Hydrate transient runtime state from host-local settings."""
	global _SYNCING_LOCAL_REMOTE_STATE
	props = get_remote_props(context)
	_SYNCING_LOCAL_REMOTE_STATE = True
	try:
		props.remote_mode = get_local_remote_mode()
	finally:
		_SYNCING_LOCAL_REMOTE_STATE = False

def update_remote_mode_state(self, context):
	"""Persist host-local mode changes and stop target services when leaving target mode."""
	if _SYNCING_LOCAL_REMOTE_STATE:
		return

	mode = str(self.remote_mode or "SOURCE").upper()
	set_local_remote_mode(mode)
	try:
		if mode != 'TARGET' and render_manager and network_manager:
			if network_manager.discovery_active:
				network_manager.stop_discovery_server(force=True)
			if network_manager.communication_active:
				network_manager.stop_communication_server(force=True)
	except Exception as e:
		print(f"Remote render mode update failed: {e}")

def start_remote_render_progress_monitoring(target_node, cancel_event=None):
	"""Start monitoring render progress and reconciling rendered outputs."""
	state = {
		'downloaded_hashes': {},
		'last_manifest_signature': None,
		'last_manifest_change': time.time(),
		'completion_observed_at': None,
		'consecutive_failures': 0,
		'in_flight': False,
	}
	state_lock = threading.Lock()
	source_project_root = file_sync_manager.get_project_root()
	target_node_id = getattr(target_node, 'node_id', f"{target_node.ip}:{target_node.port}")

	def schedule_monitor_update(update):
		def apply_update():
			context = bpy.context
			props = get_remote_props(context)
			connected_node = get_connected_remote_node(context, props)
			
			if not connected_node or connected_node.node_id != target_node_id:
				return None
			if not props.remote_monitor_render:
				return None
			
			status = update.get('status')
			if status:
				props.remote_render_status = status.get('status', 'Unknown')
				props.remote_render_progress = status.get('progress', 0.0)
				props.remote_render_elapsed_time = status.get('elapsed_time', 0.0)
				estimated = status.get('estimated_time')
				props.remote_render_estimated_time = float(estimated) if estimated is not None else 0.0
				props.remote_render_error_message = sanitize_ui_message(status.get('error_message', ''))
			
			if update.get('render_error_message') is not None:
				props.remote_render_error_message = sanitize_ui_message(update.get('render_error_message'))
			
			if update.get('render_status') is not None:
				props.remote_render_status = update.get('render_status')
			
			if update.get('sync_status') is not None:
				props.remote_sync_status = update.get('sync_status')
			
			if update.get('stop_monitor'):
				props.remote_monitor_render = False
			
			return None
		
		timer_manager.register_timer(apply_update, interval=0.1)
	
	def poll_and_sync_outputs():
		nonlocal source_project_root
		update = {}
		try:
			if cancel_event and cancel_event.is_set():
				return

			status = network_manager.get_render_status(
				target_node.ip,
				target_node.port,
				target_node.auth_token
			)

			if status:
				update['status'] = status

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
			with state_lock:
				if status is None and manifest is None:
					state['consecutive_failures'] += 1
					if state['consecutive_failures'] >= 5:
						update.update({
							'stop_monitor': True,
							'render_status': 'error',
							'render_error_message': 'Lost connection to remote target',
							'sync_status': 'Disconnected',
						})
					return

				state['consecutive_failures'] = 0

			download_count = 0
			if manifest is not None:
				manifest_signature = tuple(
					(path, entry.get('hash'), entry.get('size'), entry.get('timestamp'))
					for path, entry in sorted(manifest.items())
				)
				with state_lock:
					if manifest_signature != state['last_manifest_signature']:
						state['last_manifest_signature'] = manifest_signature
						state['last_manifest_change'] = now

				for relative_path, entry in sorted(manifest.items()):
					if cancel_event and cancel_event.is_set():
						return
					expected_hash = entry.get('hash')
					local_output_exists = False
					if source_project_root:
						try:
							local_output_exists = os.path.exists(resolve_under_root(source_project_root, relative_path))
						except PathSecurityError:
							local_output_exists = False
					with state_lock:
						already_downloaded = expected_hash and state['downloaded_hashes'].get(relative_path) == expected_hash
					if already_downloaded and local_output_exists:
						continue
					if already_downloaded and not local_output_exists:
						delete_outputs = getattr(network_manager, "delete_output_files_on_target", None)
						if callable(delete_outputs) and expected_hash:
							response = delete_outputs(
								target_node.ip,
								target_node.port,
								target_node.auth_token,
								[{"relative_path": relative_path, "hash": expected_hash}]
							)
							if response and response.get('status') == 'success':
								continue

					print(f"Syncing output from target: {relative_path}")
					try:
						try:
							success = network_manager.request_file_from_target(
								target_node.ip,
								target_node.port,
								target_node.auth_token,
								relative_path,
								entry,
								source_project_root=source_project_root,
								cancel_event=cancel_event
							)
						except TypeError:
							success = network_manager.request_file_from_target(
								target_node.ip,
								target_node.port,
								target_node.auth_token,
								relative_path,
								entry
							)
					except Exception as e:
						print(f"Failed to sync output {relative_path}: {e}")
						success = False
					if success:
						with state_lock:
							state['downloaded_hashes'][relative_path] = expected_hash
						download_count += 1
					else:
						print(f"Failed to sync output: {relative_path}")

			if download_count:
				update['sync_status'] = f"Downloading outputs ({download_count} updated)"
			else:
				render_status = status.get('status') if status else update.get('render_status')
				if render_status in ['preparing', 'rendering']:
					update['sync_status'] = "Rendering..."
				else:
					update['sync_status'] = "Downloading outputs..."

			if status and status.get('status') in ['preparing', 'rendering']:
				return

			with state_lock:
				if status and state['completion_observed_at'] is None:
					state['completion_observed_at'] = now

				if state['completion_observed_at'] is None:
					return

				quiet_reference = max(state['last_manifest_change'], state['completion_observed_at'])
				post_process_deadline = state['completion_observed_at'] + OUTPUT_SYNC_POST_PROCESS_TIMEOUT + OUTPUT_SYNC_QUIET_PERIOD
				if now < post_process_deadline or now - quiet_reference < OUTPUT_SYNC_QUIET_PERIOD:
					return

			update['stop_monitor'] = True
			update['sync_status'] = "Complete"
		finally:
			if update:
				schedule_monitor_update(update)
			with state_lock:
				state['in_flight'] = False

	def monitor_progress():
		context = bpy.context
		props = get_remote_props(context)
		connected_node = get_connected_remote_node(context, props)

		if not props.remote_monitor_render:
			return None
		if not connected_node:
			props.remote_monitor_render = False
			return None
		if connected_node.node_id != target_node_id:
			return None

		with state_lock:
			if state['in_flight']:
				return OUTPUT_SYNC_POLL_INTERVAL
			state['in_flight'] = True

		worker = threading.Thread(target=poll_and_sync_outputs, daemon=True)
		worker.start()
		worker.join(0.05)
		return OUTPUT_SYNC_POLL_INTERVAL

	timer_manager.register_timer(monitor_progress, interval=1.0, persistent=True)

# ----
# Property Groups for UI State
# ----

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
	is_connected: BoolProperty(name="Is Connected")
	auth_token: StringProperty(name="Auth Token")

class RemoteRuntimeState(PropertyGroup):
	"""Transient Render Remote state that should not be saved as preferences or project settings"""
	remote_mode: EnumProperty(
		name="Mode",
		description="Select operation mode for this computer",
		items=[
#			('SOURCE', "Source", "Control remote rendering from this computer"),
			('SOURCE', "Source", "Control remote rendering from this computer", "DESKTOP", 0),
#			('TARGET', "Target", "Allow this computer to be used for remote rendering"),
			('TARGET', "Target", "Allow this computer to be used for remote rendering", "NETWORK_DRIVE", 1),
		],
		default='SOURCE',
		update=update_remote_mode_state,
	)
	remote_source_connection_mode: EnumProperty(
		name="Connection",
		description="Select how to find the remote render target",
		items=[
			('MANUAL', "Manual", "Connect to a remote render target by IP address"),
#			('MANUAL', "Manual", "Connect to a remote render target by IP address", "NETWORK_DRIVE", 0),
			('SEARCH', "Search", "Scan the local network for remote render targets"),
#			('SEARCH', "Search", "Scan the local network for remote render targets", "VIEWZOOM", 1),
		],
		default='SEARCH',
	)
	remote_network_scanning: BoolProperty(name="Network Scanning", default=False)
	remote_sync_status: StringProperty(name="Sync Status", default="Not Scanned")
	remote_external_files_count: IntProperty(name="External Files Count", default=0)
	remote_show_external_warning: BoolProperty(name="Show External Warning", default=False)
	remote_missing_files_count: IntProperty(name="Missing Files Count", default=0)
	remote_show_missing_warning: BoolProperty(name="Show Missing Warning", default=False)
	remote_render_status: StringProperty(name="Render Status", default="Not Started")
	remote_render_progress: FloatProperty(name="Render Progress", default=0.0, min=0.0, max=100.0, subtype='PERCENTAGE')
	remote_render_elapsed_time: FloatProperty(name="Elapsed Time", default=0.0)
	remote_render_estimated_time: FloatProperty(name="Estimated Time Remaining", default=0.0)
	remote_render_error_message: StringProperty(name="Render Error", default="")
	remote_monitor_render: BoolProperty(name="Monitor Render", default=False)

# ----
# Source-side Render Remote Workflow Helpers
# ----

def get_connected_remote_node(context, props=None):
	"""Return the connected remote node, if any."""
	for node in get_discovered_nodes(context):
		if node.is_connected:
			return node
	return None

def clear_connected_remote_nodes(context, keep_node=None):
	"""Clear active source-side connections except an optional node."""
	for node in get_discovered_nodes(context):
		if keep_node is not None and node is keep_node:
			continue
		node.is_connected = False
		node.auth_token = ""

def format_connected_remote_label(node):
	"""Human-friendly label for the connected render target"""
	if not node:
		return "Not connected"
	if node.name and node.ip:
		return f"{node.name}   {node.ip}:{node.port}"
	if node.name:
		return node.name
	if node.ip:
		return f"{node.ip}:{node.port}"
	return "Connected target"

def update_sync_ui_from_scan(context, dependencies, sync_changes=None):
	"""Update sync UI state from dependency and manifest comparison results"""
	props = get_remote_props(context)
	props.remote_external_files_count = len(dependencies['external'])
	props.remote_show_external_warning = len(dependencies['external']) > 0
	props.remote_missing_files_count = len(dependencies['missing'])
	props.remote_show_missing_warning = len(dependencies['missing']) > 0

	get_sync_files(context).clear()

	if sync_changes:
		for file_info in sync_changes['new_files']:
			item = get_sync_files(context).add()
			item.file_path = file_info['path']
			item.status = 'new'
			item.size = file_info['size']

		for file_info in sync_changes['modified_files']:
			item = get_sync_files(context).add()
			item.file_path = file_info['path']
			item.status = 'modified'
			item.size = file_info['size']

		for file_info in sync_changes['deleted_files']:
			item = get_sync_files(context).add()
			item.file_path = file_info['path']
			item.status = 'deleted'
			item.size = 0

		total_files = len(sync_changes['new_files']) + len(sync_changes['modified_files'])
		if total_files:
			props.remote_sync_status = f"{total_files} files need sync"
		elif dependencies['external'] or dependencies['missing']:
			props.remote_sync_status = "Unsupported references found"
		elif sync_changes['deleted_files']:
			props.remote_sync_status = f"{len(sync_changes['deleted_files'])} stale remote files"
		else:
			props.remote_sync_status = "Up to date"
	else:
		props.remote_sync_status = "Unsupported references found" if (dependencies['external'] or dependencies['missing']) else "Up to date"

	for file_path in dependencies['external']:
		item = get_sync_files(context).add()
		item.file_path = file_path
		item.status = 'external'
		item.size = 0
		item.selected = False

	for file_path in dependencies['missing']:
		item = get_sync_files(context).add()
		item.file_path = file_path
		item.status = 'missing'
		item.size = 0
		item.selected = False

def collect_project_sync_state(props, target_node, require_remote_manifest=True):
	"""Scan dependencies and compare them with the target-owned input manifest"""
	project_root = file_sync_manager.get_project_root()
	if not project_root:
		raise Exception("Could not determine project root")

	project_cache_name = build_source_project_cache_name()
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

def sync_project_inputs_to_target(target_node, project_cache_name, project_root, local_manifest, sync_changes, upload_paths=None, delete_paths=None, status_callback=None, cancel_event=None):
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
		raise_if_workflow_cancelled(cancel_event)
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
			local_manifest[relative_path],
			cancel_event=cancel_event
		)

		if success:
			result['uploaded'] += 1
		else:
			result['failed_uploads'].append(relative_path)
		raise_if_workflow_cancelled(cancel_event)

	if delete_paths:
		raise_if_workflow_cancelled(cancel_event)
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
		raise_if_workflow_cancelled(cancel_event)

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

def schedule_remote_status_update(sync_status=None, render_status=None, render_error_message=None, monitor_render=None, workflow_id=None, cancel_event=None):
	"""Schedule a UI-safe status update on the main thread"""
	def update():
		if workflow_id is not None and not is_current_remote_workflow(workflow_id):
			return None
		if cancel_event and cancel_event.is_set():
			return None
		context = bpy.context
		if not context:
			return None

		props = get_remote_props(context)
		if sync_status is not None:
			props.remote_sync_status = sync_status
		if render_status is not None:
			props.remote_render_status = render_status
		if render_error_message is not None:
			props.remote_render_error_message = sanitize_ui_message(render_error_message)
		if monitor_render is not None:
			props.remote_monitor_render = monitor_render
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

def draw_progress_indicator(layout, props):
	"""Draw a non-interactive render progress indicator with a compatibility fallback."""
	progress = max(0.0, min(100.0, float(props.remote_render_progress or 0.0)))
	if hasattr(layout, "progress"):
		try:
			layout.progress(factor=progress / 100.0, type='BAR', text="")
			return
		except TypeError:
			pass

	row = layout.row()
	row.enabled = False
	row.prop(props, "remote_render_progress", text="", slider=True)

# ----
# Operators (keeping existing ones but simplifying some logic)
# ----

class REMOTERENDER_OT_StartDiscovery(Operator):
	bl_idname = "render_remote.start_discovery"
	bl_label = "Allow Remote Rendering"
	bl_description = "Start the LAN listening service that allows other computers to send remote render jobs"

	def execute(self, context):
		prefs = context.preferences.addons[ADDON_PACKAGE].preferences
		node_name = get_remote_node_name()

		if network_manager.discovery_active:
			self.report({'WARNING'}, "Discovery already active")
			return {'CANCELLED'}

		if not prefs.remote_passcode:
			self.report({'ERROR'}, "Set a Render Remote authentication passcode in add-on preferences before starting target mode")
			return {'CANCELLED'}

		network_manager.update_ports_from_preferences()

		if not network_manager.start_discovery_server(
			node_name,
			prefs.remote_passcode
		):
			self.report({'ERROR'}, "Failed to start authenticated remote render target")
			return {'CANCELLED'}

		self.report({'INFO'}, f"Remote render target enabled for {node_name}")
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
		get_remote_props(context).remote_network_scanning = True

		def scan_network():
			discovered = network_manager.discover_nodes()

			def update_ui():
				context = bpy.context
				props = get_remote_props(context)
				get_discovered_nodes(context).clear()

				for node_id, node_info in discovered.items():
					item = get_discovered_nodes(context).add()
					item.node_id = node_id
					item.name = node_info['name']
					item.ip = node_info['ip']
					item.port = node_info['port']
					item.blender_version = node_info['blender_version']

				props.remote_network_scanning = False
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
		from .constants import is_allowed_lan_ip
		props = get_remote_props(context)
		prefs = get_remote_preferences(context)

		# Find the node to connect to
		target_node = None
		for node in get_discovered_nodes(context):
			if node.node_id == self.node_id:
				target_node = node
				break

		if not target_node:
			self.report({'ERROR'}, "Node not found")
			return {'CANCELLED'}

		if not is_allowed_lan_ip(target_node.ip):
			self.report({'ERROR'}, "Remote node is not on an allowed LAN address")
			return {'CANCELLED'}

		if not prefs.remote_connection_password:
			self.report({'ERROR'}, "Password required for this node")
			return {'CANCELLED'}

		auth_token = network_manager.authenticate(
			target_node.ip,
			target_node.port,
			prefs.remote_connection_password
		)

		if not auth_token:
			detail = sanitize_ui_message(network_manager.last_error)
			self.report({'ERROR'}, detail or "Authentication failed - check password")
			return {'CANCELLED'}

		# Test connection
		if network_manager.test_connection(target_node.ip, target_node.port, auth_token):
			clear_connected_remote_nodes(context, keep_node=target_node)
			target_node.is_connected = True
			target_node.auth_token = auth_token or ""

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
		props = get_remote_props(context)

		clear_connected_remote_nodes(context)
		props.remote_sync_status = "Not Scanned"
		props.remote_monitor_render = False
		props.remote_render_status = "Not Started"
		props.remote_render_error_message = ""
		self.report({'INFO'}, "Disconnected from remote node")
		return {'FINISHED'}

class REMOTERENDER_OT_ConnectManual(Operator):
	bl_idname = "render_remote.connect_manual"
	bl_label = "Connect"
	bl_description = "Connect to manually entered IP address"

	def execute(self, context):
		from .constants import is_allowed_lan_ip
		props = get_remote_props(context)
		prefs = get_remote_preferences(context)

		if not prefs.remote_manual_ip:
			self.report({'ERROR'}, "Please enter an IP address")
			return {'CANCELLED'}

		if not is_allowed_lan_ip(prefs.remote_manual_ip):
			self.report({'ERROR'}, "Manual IP must be a private, link-local, or loopback address")
			return {'CANCELLED'}

		if not prefs.remote_connection_password:
			self.report({'ERROR'}, "Password required for remote render nodes")
			return {'CANCELLED'}

		auth_token = network_manager.authenticate(
			prefs.remote_manual_ip,
			prefs.remote_manual_port,
			prefs.remote_connection_password
		)

		if not auth_token:
			detail = sanitize_ui_message(network_manager.last_error)
			self.report({'ERROR'}, detail or "Authentication failed - check password")
			return {'CANCELLED'}

		if network_manager.test_connection(prefs.remote_manual_ip, prefs.remote_manual_port, auth_token):
			nodes = get_discovered_nodes(context)
			manual_node = None
			manual_node_index = None
			duplicate_indices = []
			manual_node_id = f"{prefs.remote_manual_ip}:{prefs.remote_manual_port}"

			for index, node in enumerate(nodes):
				if node.ip == prefs.remote_manual_ip and node.port == prefs.remote_manual_port:
					if manual_node is None:
						manual_node = node
						manual_node_index = index
					elif manual_node.name.startswith("Manual (") and not node.name.startswith("Manual ("):
						duplicate_indices.append(manual_node_index)
						manual_node = node
						manual_node_index = index
					else:
						duplicate_indices.append(index)

			for index in reversed(duplicate_indices):
				nodes.remove(index)

			if manual_node is None:
				manual_node = nodes.add()
				manual_node.node_id = manual_node_id
				manual_node.name = f"Manual ({prefs.remote_manual_ip})"
				manual_node.ip = prefs.remote_manual_ip
				manual_node.port = prefs.remote_manual_port

			clear_connected_remote_nodes(context, keep_node=manual_node)
			manual_node.is_connected = True
			manual_node.auth_token = auth_token or ""

			self.report({'INFO'}, f"Connected to {prefs.remote_manual_ip}")
		else:
			self.report({'ERROR'}, f"Failed to connect to {prefs.remote_manual_ip}")
			return {'CANCELLED'}

		return {'FINISHED'}

class REMOTERENDER_OT_ScanProject(Operator):
	bl_idname = "render_remote.scan_project"
	bl_label = "Scan Project Dependencies"
	bl_description = "Scan current project for all file dependencies and check sync status"

	def execute(self, context):
		props = get_remote_props(context)

		if not bpy.data.filepath:
			self.report({'ERROR'}, "Please save your blend file first")
			return {'CANCELLED'}

		props.remote_sync_status = "Scanning inputs..."
		self.report({'INFO'}, "Scanning project dependencies...")

		def scan_project():
			try:
				context = bpy.context
				props = get_remote_props(context)
				dependencies = file_sync_manager.scan_blend_dependencies()
				sync_changes = None
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
					props = get_remote_props(context)
					props.remote_sync_status = f"Scan failed: {sanitize_ui_message(e)}"
					return None

				timer_manager.register_timer(update_error, interval=0.1)

		threading.Thread(target=scan_project, daemon=True).start()

		return {'FINISHED'}

class REMOTERENDER_OT_SyncFiles(Operator):
	bl_idname = "render_remote.sync_files"
	bl_label = "Sync Selected Files"
	bl_description = "Sync selected files to remote node"

	def execute(self, context):
		props = get_remote_props(context)

		if not bpy.data.filepath:
			self.report({'ERROR'}, "Please save your blend file first")
			return {'CANCELLED'}

		target_node = get_connected_remote_node(context, props)
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}

		# Get selected files and stale owned inputs
		selected_upload_paths = [
			normalize_relative_path(f.file_path)
			for f in get_sync_files(context)
			if f.selected and f.status in {'new', 'modified'}
		]
		selected_delete_paths = [
			normalize_relative_path(f.file_path)
			for f in get_sync_files(context)
			if f.selected and f.status == 'deleted'
		]

		if not selected_upload_paths and not selected_delete_paths:
			self.report({'WARNING'}, "No files selected for sync")
			return {'CANCELLED'}

		props.remote_sync_status = "Scanning inputs..."
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
					props = get_remote_props(context)
					if sync_result['delete_total']:
						props.remote_sync_status = f"Synced {sync_result['uploaded']}/{sync_result['upload_total']} files, deleted {sync_result['deleted']}/{sync_result['delete_total']} stale inputs"
					else:
						props.remote_sync_status = f"Synced {sync_result['uploaded']}/{sync_result['upload_total']} files"

					if sync_result['failed_uploads'] or sync_result['failed_deletes']:
						props.remote_sync_status = f"{props.remote_sync_status} with errors"
					else:
						props.remote_sync_status = "Complete"

					if sync_result['uploaded'] > 0 or sync_result['deleted'] > 0:
						bpy.ops.render_remote.scan_project()

					return None

				timer_manager.register_timer(update_ui, interval=0.1)

			except Exception as e:
				print(f"File sync failed: {e}")

				def update_error():
					context = bpy.context
					props = get_remote_props(context)
					props.remote_sync_status = f"Sync failed: {sanitize_ui_message(e)}"
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
		props = get_remote_props(context)

		if not bpy.data.filepath:
			self.report({'ERROR'}, "Please save your blend file first")
			return {'CANCELLED'}

		target_node = get_connected_remote_node(context, props)
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}

		target_node = SimpleNamespace(
			node_id=target_node.node_id,
			name=target_node.name,
			ip=target_node.ip,
			port=target_node.port,
			auth_token=target_node.auth_token,
		)
		workflow_id, cancel_event = begin_remote_workflow()
		animation = self.animation
		props.remote_sync_status = "Scanning inputs..."
		props.remote_render_status = "preparing"
		props.remote_monitor_render = False
		props.remote_render_error_message = ""
		self.report({'INFO'}, "Preparing remote render: scanning and syncing inputs...")

		def start_render_workflow():
			try:
				raise_if_workflow_cancelled(cancel_event)
				context = bpy.context
				props = get_remote_props(context)
				scene = context.scene
				sync_state = collect_project_sync_state(props, target_node)
				raise_if_workflow_cancelled(cancel_event)
				dependencies = sync_state['dependencies']
				sync_changes = sync_state['sync_changes']

				def update_scanned():
					if not is_current_remote_workflow(workflow_id) or cancel_event.is_set():
						return None
					context = bpy.context
					props = get_remote_props(context)
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
					status_callback=lambda message: schedule_remote_status_update(
						sync_status=message,
						render_status="preparing",
						workflow_id=workflow_id,
						cancel_event=cancel_event
					),
					cancel_event=cancel_event
				)
				raise_if_workflow_cancelled(cancel_event)

				if sync_result['failed_uploads']:
					raise Exception(f"Failed to sync {len(sync_result['failed_uploads'])} input files")

				if sync_result['failed_deletes']:
					raise Exception(f"Failed to delete {len(sync_result['failed_deletes'])} stale remote inputs")

				def update_starting():
					if not is_current_remote_workflow(workflow_id) or cancel_event.is_set():
						return None
					context = bpy.context
					props = get_remote_props(context)
					props.remote_sync_status = "Rendering..."
					props.remote_render_status = "preparing"
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
				if cancel_event.is_set():
					network_manager.cancel_remote_render(
						target_node.ip,
						target_node.port,
						target_node.auth_token
					)
					raise RemoteWorkflowCancelled()

				if not result or result.get('status') != 'success':
					error_msg = result.get('message', 'Unknown error') if result else 'Connection failed'
					raise Exception(f"Failed to start render: {error_msg}")

				def update_success():
					if not is_current_remote_workflow(workflow_id) or cancel_event.is_set():
						return None
					context = bpy.context
					props = get_remote_props(context)
					props.remote_render_status = "preparing"
					props.remote_monitor_render = True
					start_remote_render_progress_monitoring(target_node, cancel_event=cancel_event)
					return None

				timer_manager.register_timer(update_success, interval=0.1)

			except RemoteWorkflowCancelled:
				def update_cancelled():
					if not is_current_remote_workflow(workflow_id):
						return None
					context = bpy.context
					props = get_remote_props(context)
					props.remote_render_status = "cancelled"
					props.remote_monitor_render = False
					props.remote_sync_status = "Cancelled"
					return None

				timer_manager.register_timer(update_cancelled, interval=0.1)

			except Exception as e:
				error_message = str(e)
				print(f"Remote render preparation failed: {error_message}")

				def update_error():
					if not is_current_remote_workflow(workflow_id) or cancel_event.is_set():
						return None
					context = bpy.context
					props = get_remote_props(context)
					props.remote_render_status = "error"
					props.remote_render_error_message = sanitize_ui_message(error_message)
					props.remote_monitor_render = False
					props.remote_sync_status = f"Render preparation failed: {sanitize_ui_message(error_message)}"
					return None

				timer_manager.register_timer(update_error, interval=0.1)

		threading.Thread(target=start_render_workflow, daemon=True).start()
		return {'FINISHED'}

	def _start_progress_monitoring(self, context, target_node):
		"""Start monitoring render progress and reconciling rendered outputs"""
		start_remote_render_progress_monitoring(target_node)

class REMOTERENDER_OT_CancelRemoteRender(Operator):
	bl_idname = "render_remote.cancel_remote_render"
	bl_label = "Cancel Remote Render"
	bl_description = "Cancel rendering on remote computer"

	def execute(self, context):
		props = get_remote_props(context)
		cancel_remote_workflows()

		target_node = get_connected_remote_node(context, props)
		if not target_node:
			props.remote_render_status = "cancelled"
			props.remote_sync_status = "Cancelled"
			props.remote_monitor_render = False
			self.report({'INFO'}, "Local remote render workflow cancelled")
			return {'CANCELLED'}

		# Send cancel request
		success = network_manager.cancel_remote_render(
			target_node.ip,
			target_node.port,
			target_node.auth_token
		)

		if success:
			self.report({'INFO'}, "Render cancelled")
			props.remote_render_status = "cancelled"
			props.remote_sync_status = "Cancelled"
			props.remote_monitor_render = False
		else:
			self.report({'ERROR'}, "Failed to cancel render")
			props.remote_render_status = "cancelled"
			props.remote_sync_status = "Cancel requested"
			props.remote_monitor_render = False

		return {'FINISHED'}

class REMOTERENDER_OT_SelectAllSyncFiles(Operator):
	bl_idname = "render_remote.select_all_sync_files"
	bl_label = "Select All"
	bl_description = "Select all files for synchronization"

	def execute(self, context):
		for sync_file in get_sync_files(context):
			sync_file.selected = True
		return {'FINISHED'}

class REMOTERENDER_OT_DeselectAllSyncFiles(Operator):
	bl_idname = "render_remote.deselect_all_sync_files"
	bl_label = "Deselect All"
	bl_description = "Deselect all files for synchronization"

	def execute(self, context):
		for sync_file in get_sync_files(context):
			sync_file.selected = False
		return {'FINISHED'}

class REMOTERENDER_OT_ClearCache(Operator):
	bl_idname = "render_remote.clear_cache"
	bl_label = "Clear Local Cache"
	bl_description = "Clear local cache directory"

	def execute(self, context):
		prefs = context.preferences.addons[ADDON_PACKAGE].preferences
		cache_dir = network_manager._resolve_cache_root(prefs.remote_cache_directory)

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
			return context.preferences.addons[ADDON_PACKAGE].preferences.remote_enable
		except (AttributeError, KeyError):
			return False
	
	def draw(self, context):
		layout = self.layout
		props = get_remote_props(context)
		prefs = context.preferences.addons[ADDON_PACKAGE].preferences
		
		# Mode Selection
		layout.prop(props, "remote_mode", expand=True, icon='SETTINGS')
		
		layout.separator()
		
		# Dynamic UI based on selected mode
		if props.remote_mode == 'TARGET':
			self.draw_target_mode(layout, props, prefs)
		else:  # SOURCE mode
			self.draw_source_mode(layout, props, prefs)
	
	
	
	########## TARGET MODE ##########
	
	def draw_target_mode(self, layout, props, prefs):
		"""Draw UI for Target mode"""
		from .render import render_manager
		box = layout.box()
		
		# Info placement
		info = box.row()
		# Button placement
		button = box.row()
		# Status placement
		status = box.row()
		
		# Enable/Disable Target
		if network_manager.discovery_active:
			button.operator("render_remote.stop_discovery", icon='PAUSE')
		else:
			button.operator("render_remote.start_discovery", icon='PLAY')
		
		# Status content — three states when active
		if network_manager.discovery_active:
			if network_manager.is_rendering:
				status.label(text="Remote render in progress", icon='RENDER_ANIMATION')
			else:
				source_name = network_manager.get_connected_source_name()
				if source_name:
					status.label(text=f"Connected: {source_name}", icon='LINKED')
				else:
					status.label(text="Listening for LAN remote render jobs", icon='CHECKMARK')
		else:
			status.label(text="Not listening for remote render jobs", icon='PAUSE') # PAUSE BLANK1
		
		# Info content and Button disable
		if not prefs.remote_passcode:
			info.label(text="Set passcode in Preferences before allowing remote rendering.", icon='PREFERENCES')
			button.active = False
			button.enabled = False
		else:
			info.label(text=f'{get_remote_node_name()}   {get_local_lan_ip()}:{network_manager.communication_port}', icon='NETWORK_DRIVE')
		
		# Render progress when a remote render is running on this target
		if network_manager.discovery_active and network_manager.is_rendering:
			progress_box = box.box()
			render_progress = render_manager.render_progress
			render_status = render_manager.render_status
			finalizing = render_status == 'completed'
			
			# Finalizing label
			if finalizing:
				progress_box.label(text="Finalizing outputs...", icon='FILE_REFRESH')
			
			# Progress Bar
			if render_progress > 0:
				from types import SimpleNamespace
				proxy = SimpleNamespace(remote_render_progress=render_progress)
				draw_progress_indicator(progress_box, proxy)
			
			# Elapsed Time, Progress Percentage, Estimated Time
			if render_progress > 0 or render_manager.render_start_time:
				grid = progress_box.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True, align=False)
				
				# Elapsed Time — frozen at render end when finalizing
				if render_manager.render_start_time:
					if finalizing:
						elapsed_time = render_manager.render_elapsed_time or (time.time() - render_manager.render_start_time)
					else:
						elapsed_time = time.time() - render_manager.render_start_time
					elapsed_minutes = int(elapsed_time // 60)
					elapsed_seconds = int(elapsed_time % 60)
					grid.label(text=f"{elapsed_minutes:02d}:{elapsed_seconds:02d}")
				else:
					grid.separator()
				
				# Progress Percentage
				if render_progress > 0:
					grid.label(text=f"{render_progress:.1f}%")
				else:
					grid.separator()
				
				# Estimated Time — from target computation, hidden when finalizing
				estimated_time = render_manager.render_estimated_time
				if not finalizing and estimated_time is not None and estimated_time > 0 and render_progress < 100:
					estimated_minutes = int(estimated_time // 60)
					estimated_seconds = int(estimated_time % 60)
					grid.label(text=f"{estimated_minutes:02d}:{estimated_seconds:02d}")
				else:
					grid.separator()
			
			# Error message
			if render_manager.render_error_message:
				error_box = progress_box.box()
				error_box.alert = True
				error_box.label(text=f"Error: {sanitize_ui_message(render_manager.render_error_message)}", icon='ERROR')
	
	
	
	########## SOURCE MODE: NETWORK ##########
	
	def draw_source_mode(self, layout, props, prefs):
		"""Draw UI for Source mode"""
		context = bpy.context
		
		# Skip if file is unsaved
		if not bpy.data.filepath:
			layout.label(text="Save project to continue", icon='ERROR')
			return
		
		# General setup
		connected_node = get_connected_remote_node(context, props)
		box = layout.box()
		
		# Status
		if connected_node:
			box.label(text=f"{format_connected_remote_label(connected_node)}", icon='LINKED')
		else:
			box.label(text="Not connected", icon='UNLINKED')
		
		grid = box.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=False)
		subrow = grid.row(align=True)
		subrow.prop(props, "remote_source_connection_mode", expand=True)
		
		# Search mode
		if props.remote_source_connection_mode == 'SEARCH':
			# Network scan
			grid.operator("render_remote.scan_network", icon='VIEWZOOM')

			# Discovered nodes, otherwise scanning status or blank separator
			if get_discovered_nodes(context):
				for node in get_discovered_nodes(context):
					node_box = box.box()
					col = node_box.column(align=True)
					row1 = col.row(align=True)
					row2 = col.row(align=True)
					
					# Node info
					node_label = f"{node.name}"
#					node_label += f"   {node.ip}:{node.port}"
					if node.blender_version: node_label += f"   Blender {node.blender_version}"
					row1.label(text=node_label, icon="NETWORK_DRIVE")
					
					# Connection controls
					if node.is_connected:
						row2.label(text="Connected", icon='CHECKMARK')
						row2.operator("render_remote.disconnect_node", text="Disconnect")
					else:
						row2.prop(prefs, "remote_connection_password", text="")
						op = row2.operator("render_remote.connect_node", text="Connect")
						op.node_id = node.node_id
			
			# Scanning status label (occupies grid cell; replaced by spacer when idle)
			elif props.remote_network_scanning:
				grid.label(text="Scanning...", icon='SORTTIME')
			
			# Spacer when no scan has been performed yet
			else:
				grid.separator()
		
		# Manual mode
		else:
			# Grid spacer
			grid.separator()
			
			# Manual connection
			grid = box.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
			grid.prop(prefs, "remote_manual_ip", text="")
			grid.prop(prefs, "remote_manual_port", text="")
			if connected_node:
				grid.label(text="Connected", icon='CHECKMARK')
				grid.operator("render_remote.disconnect_node", text="Disconnect")
			else:
				grid.prop(prefs, "remote_connection_password", text="")
				grid.operator("render_remote.connect_manual", text="Connect")
		
		# Project scanning and sync
		layout.separator()
		if get_connected_remote_node(context, props):
			self.draw_sync_interface(layout, context, props)
		else:
			layout.label(text="Connect target to continue", icon='ERROR')
	
	
	
	# SOURCE MODE: SYNC #
	
	def draw_sync_interface(self, layout, context, props):
		"""Draw file synchronization interface"""
		box = layout.box()
		
		# Show project root directory
		project_root = file_sync_manager.get_project_root()
		if project_root:
			box.label(text=f"Sync root:  {os.path.basename(project_root)}/", icon='FILE_REFRESH') # FILE_FOLDER FILE_REFRESH
		else:
			box.label(text="Project root not found", icon='ERROR')
		
		# Scan button and status
		box.operator("render_remote.scan_project", icon='VIEWZOOM')
		
		# External files warning
		if props.remote_show_external_warning:
			warning_box = box.box()
			warning_box.alert = True
			warning_box.label(text=f"Warning: {props.remote_external_files_count} external files detected!", icon='ERROR')
			warning_box.label(text="External files will NOT be synced to target computer.")
			warning_box.label(text="Only files within the project folder structure are supported.")
		
		if props.remote_show_missing_warning:
			warning_box = box.box()
			warning_box.alert = True
			warning_box.label(text=f"Warning: {props.remote_missing_files_count} referenced files are missing!", icon='ERROR')
			warning_box.label(text="Missing files must be restored before remote rendering.")
		
		# Sync files list
		if get_sync_files(context):
			# File list
			sync_box = box.box()
			for sync_file in get_sync_files(context):
				row = sync_box.row(align=True)
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
				subrow = col.row(align=False)
				subrow.label(text=sync_file.file_path)
				if sync_file.size > 0:
					size_mb = sync_file.size / (1024 * 1024)
					if size_mb < 1:
						size_str = f"{sync_file.size / 1024:.1f} KB"
					else:
						size_str = f"{size_mb:.1f} MB"
					subrow.label(text=f"{size_str}")
				else:
					subrow.label(text=sync_file.status.upper())
			
#			row = box.row()
#			row.label(text=f"{props.remote_sync_status}", icon='INFO')
			
			# Select all/none buttons, Sync button
			grid = box.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=False)
			subrow = grid.row(align=True)
			subrow.operator("render_remote.deselect_all_sync_files", text="None", icon="CHECKBOX_DEHLT")
			subrow.operator("render_remote.select_all_sync_files", text="All", icon="CHECKBOX_HLT")
			grid.operator("render_remote.sync_files", text="Sync", icon='FILE_REFRESH')
		
		elif props.remote_sync_status == "Up to date":
			box.label(text="All dependencies are synced", icon='CHECKMARK')
		else:
			box.label(text="")
		
		# Render Management Interface
		if get_connected_remote_node(context, props):
			layout.separator()
			self.draw_render_interface(layout, context, props)
	
	
	
	# SOURCE MODE: RENDER #
	
	def draw_render_interface(self, layout, context, props):
		"""Draw render management interface"""
		box = layout.box()
		
		# Status
		box.label(text=f"Render: {format_render_status_label(props.remote_render_status)}", icon='RENDER_ANIMATION')
		
		active_workflow = props.remote_monitor_render or props.remote_render_status in ['preparing', 'rendering']
		
		# Render controls
		if active_workflow:
			if props.remote_render_status in ['preparing', 'rendering']:
				box.operator("render_remote.cancel_remote_render", icon='X')
			
			progress_box = box.box()
			progress_box.label(text=f"Status: {props.remote_sync_status}") # , icon='INFO'
			
			# Progress Bar
			if props.remote_render_progress > 0:
				draw_progress_indicator(progress_box, props)
			
			# Elapsed Time, Progress Percentage, Estimated Time
			render_progress = float(props.remote_render_progress)
			if render_progress > 0 or props.remote_render_elapsed_time > 0:
				grid = progress_box.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True, align=False)
				
				# Elapsed Time
				if props.remote_render_elapsed_time > 0:
					elapsed_time = props.remote_render_elapsed_time
					elapsed_minutes = int(elapsed_time // 60)
					elapsed_seconds = int(elapsed_time % 60)
					grid.label(text=f"{elapsed_minutes:02d}:{elapsed_seconds:02d}")
				else:
					grid.separator()
				
				# Progress Percentage
				if render_progress > 0:
					grid.label(text=f"{render_progress:.1f}%")
				else:
					grid.separator()
				
				# Estimated Time — from target computation
				estimated_time = props.remote_render_estimated_time
				if estimated_time > 0 and render_progress < 100:
					estimated_minutes = int(estimated_time // 60)
					estimated_seconds = int(estimated_time % 60)
					grid.label(text=f"{estimated_minutes:02d}:{estimated_seconds:02d}")
				else:
					grid.separator()
			
			if props.remote_monitor_render and props.remote_render_status not in ['preparing', 'rendering']:
				progress_box.label(text="Waiting for output sync to settle.", icon='INFO')
			
			if props.remote_render_error_message:
				error_box = progress_box.box()
				error_box.alert = True
				error_box.label(text=f"Error: {props.remote_render_error_message}", icon='ERROR')
		else:
			if props.remote_show_external_warning or props.remote_show_missing_warning:
				warning_box = box.box()
				warning_box.alert = True
				warning_box.label(text="Resolve missing or unsupported references before rendering.", icon='ERROR')
			
			row = box.row()
			row.enabled = not (props.remote_show_external_warning or props.remote_show_missing_warning)
			op = row.operator("render_remote.start_remote_render", text="Render Animation", icon='RENDER_ANIMATION')
			op.animation = True
			
			# Show error if available
			if props.remote_render_status and props.remote_render_status != "Not Started" and props.remote_render_error_message:
				status_box = box.box()
				status_box.label(text=f"Error: {props.remote_render_error_message}", icon='ERROR')
