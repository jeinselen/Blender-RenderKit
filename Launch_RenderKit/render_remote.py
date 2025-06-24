import bpy
import socket
import ssl
import json
import hashlib
import secrets
import threading
import time
import os
import shutil
import struct
from datetime import datetime, timedelta
from pathlib import Path
from bpy.props import StringProperty, EnumProperty, BoolProperty, IntProperty, FloatProperty, CollectionProperty
from bpy.types import Operator, Panel, AddonPreferences, PropertyGroup
from bpy.app.handlers import persistent

# Add bl_info for version reference
bl_info = {
	'version': (1, 0, 0),
}

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
			return False  # Already registered
		
		def wrapper():
			try:
				# Check if callback was cancelled
				if callback not in self.active_timers:
					return None  # Timer was cancelled
				
				result = callback()
				
				# Handle different return values
				if result is None:
					# Callback wants to stop
					self.unregister_timer(callback)
					return None
				elif result is False:
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
# File Synchronization Manager
# ----

class FileSyncManager:
	"""Handles file synchronization between source and target computers"""
	
	def __init__(self):
		self.chunk_size = 64 * 1024  # 64KB chunks for file transfer
		self.max_file_size = 500 * 1024 * 1024  # 500MB max file size
		
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
			abs_file_path = os.path.abspath(file_path)
			abs_project_root = os.path.abspath(project_root)
			
			# Check if file is within project root
			return abs_file_path.startswith(abs_project_root)
		except:
			return False
	
	def scan_blend_dependencies(self, blend_file_path=None):
		"""Scan blend file for all dependencies and categorize them"""
		if not blend_file_path:
			blend_file_path = bpy.data.filepath
			
		if not blend_file_path:
			return {'internal': [], 'external': [], 'missing': []}
		
		project_root = self.get_project_root(blend_file_path)
		dependencies = {'internal': [], 'external': [], 'missing': []}
		
		# Always include the blend file itself as an internal dependency
		if os.path.exists(blend_file_path) and self.validate_file_scope(blend_file_path, project_root):
			dependencies['internal'].append(blend_file_path)
		
		# Collect all file references from Blender
		file_paths = set()
		
		# Images
		for img in bpy.data.images:
			if img.filepath and not img.packed_file:
				file_paths.add(bpy.path.abspath(img.filepath))
		
		# Sounds
		for sound in bpy.data.sounds:
			if sound.filepath and not sound.packed_file:
				file_paths.add(bpy.path.abspath(sound.filepath))
				
		# Movie clips
		for clip in bpy.data.movieclips:
			if clip.filepath:
				file_paths.add(bpy.path.abspath(clip.filepath))
				
		# Fonts
		for font in bpy.data.fonts:
			if font.filepath:
				file_paths.add(bpy.path.abspath(font.filepath))
				
		# Libraries (linked files)
		for lib in bpy.data.libraries:
			if lib.filepath:
				file_paths.add(bpy.path.abspath(lib.filepath))
		
		# Cache files (simulation caches, etc.)
		for obj in bpy.data.objects:
			for modifier in obj.modifiers:
				if hasattr(modifier, 'filepath') and modifier.filepath:
					file_paths.add(bpy.path.abspath(modifier.filepath))
		
		# Check for particle cache files
		for obj in bpy.data.objects:
			for modifier in obj.modifiers:
				if modifier.type == 'PARTICLE_SYSTEM':
					psys = modifier.particle_system
					if psys.settings.type == 'HAIR':
						continue
					# Point cache files
					if hasattr(psys, 'point_cache') and psys.point_cache.filepath:
						file_paths.add(bpy.path.abspath(psys.point_cache.filepath))
		
		# Categorize files (excluding the blend file since we already added it)
		for file_path in file_paths:
			# Skip the blend file itself since we already added it
			if file_path == blend_file_path:
				continue
				
			if not os.path.exists(file_path):
				dependencies['missing'].append(file_path)
			elif self.validate_file_scope(file_path, project_root):
				dependencies['internal'].append(file_path)
			else:
				dependencies['external'].append(file_path)
				
		return dependencies
	
	def calculate_file_hash(self, file_path):
		"""Calculate SHA-256 hash of a file"""
		try:
			hash_sha256 = hashlib.sha256()
			with open(file_path, "rb") as f:
				for chunk in iter(lambda: f.read(4096), b""):
					hash_sha256.update(chunk)
			return hash_sha256.hexdigest()
		except:
			return None
	
	def get_referenced_files_manifest(self, project_root, dependencies):
		"""Create a manifest of only referenced files with hashes and metadata"""
		manifest = {}
		
		try:
			# Include all internal files (which now includes the blend file itself)
			for file_path in dependencies['internal']:
				if os.path.exists(file_path):
					rel_path = os.path.relpath(file_path, project_root)
					
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
			print(f"Error creating referenced files manifest: {e}")
			
		return manifest
	
	def get_directory_manifest(self, directory_path):
		"""Create a manifest of all files in directory with hashes and metadata"""
		manifest = {}
		
		try:
			for root, dirs, files in os.walk(directory_path):
				for file in files:
					file_path = os.path.join(root, file)
					rel_path = os.path.relpath(file_path, directory_path)
					
					# Skip certain file types
					if file.lower().endswith(('.tmp', '.log', '.blend1', '.blend2')):
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
	
	def compare_manifests(self, local_manifest, remote_manifest):
		"""Compare local and remote manifests to find differences"""
		changes = {
			'new_files': [],
			'modified_files': [],
			'deleted_files': [],
			'unchanged_files': []
		}
		
		# Files that exist locally
		for rel_path, local_info in local_manifest.items():
			if rel_path not in remote_manifest:
				changes['new_files'].append({
					'path': rel_path,
					'size': local_info['size'],
					'local_info': local_info
				})
			elif local_info['hash'] != remote_manifest[rel_path]['hash']:
				changes['modified_files'].append({
					'path': rel_path,
					'size': local_info['size'],
					'local_info': local_info,
					'remote_info': remote_manifest[rel_path]
				})
			else:
				changes['unchanged_files'].append(rel_path)
		
		# Files that exist remotely but not locally (deleted)
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
	"""Handles secure SSL connections with authentication"""
	
	def __init__(self):
		self.auth_tokens = {}
		self.connection_timeout = 300  # 5 minutes
	
	def generate_auth_token(self):
		"""Generate a secure authentication token"""
		return secrets.token_urlsafe(32)
	
	def create_ssl_context(self, is_server=False):
		"""Create SSL context for secure connections"""
		context = ssl.create_default_context()
		context.check_hostname = False
		context.verify_mode = ssl.CERT_NONE
		return context
	
	def hash_password(self, password, salt=None):
		"""Hash password with salt for secure storage"""
		if salt is None:
			salt = secrets.token_hex(16)
		return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 10000), salt
	
	def verify_password(self, password, hashed_password, salt):
		"""Verify password against hash"""
		test_hash, _ = self.hash_password(password, salt)
		return test_hash == hashed_password

# ----
# Network Discovery and Communication
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
		self.is_rendering = False  # Track if we're currently rendering
	
	def update_ports_from_preferences(self):
		"""Update ports from addon preferences if available"""
		try:
			prefs = bpy.context.preferences.addons[__package__].preferences
			self.discovery_port = prefs.remote_discovery_port
			self.communication_port = prefs.remote_communication_port
		except (AttributeError, KeyError):
			pass
	
	def start_discovery_server(self, node_name, passcode=""):
		"""Start discovery server to announce this node"""
		if self.discovery_active:
			return
		
		# Store password hash for authentication
		if passcode:
			self.stored_password_hash, self.stored_salt = self.security.hash_password(passcode)
		else:
			self.stored_password_hash = None
			self.stored_salt = None
			
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
	
	def stop_discovery_server(self):
		"""Stop discovery server"""
		# Don't stop if we're actively rendering
		if self.is_rendering:
			print("Skipping discovery server stop - rendering in progress")
			return
			
		self._shutdown_requested = True
		self.discovery_active = False
		
		if self.discovery_thread and self.discovery_thread.is_alive():
			self.discovery_thread.join(timeout=2)
			
		self.stop_communication_server()
		print("Discovery server stopped")
	
	def start_communication_server(self):
		"""Start communication server for handling connections"""
		if self.communication_active:
			return
		
		self.update_ports_from_preferences()
		
		self.communication_active = True
		self.communication_thread = threading.Thread(
			target=self._communication_server_loop,
			daemon=True
		)
		self.communication_thread.start()
		print(f"Communication server started on port {self.communication_port}")
	
	def stop_communication_server(self):
		"""Stop communication server"""
		# Don't stop if we're actively rendering
		if self.is_rendering:
			print("Skipping communication server stop - rendering in progress")
			return
			
		self.communication_active = False
		if self.communication_thread and self.communication_thread.is_alive():
			self.communication_thread.join(timeout=2)
		print("Communication server stopped")
	
	def _discovery_server_loop(self, node_name, requires_auth):
		"""Discovery server main loop"""
		sock = None
		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			sock.bind(('', self.discovery_port))
			sock.settimeout(1.0)
			
			while self.discovery_active and not self._shutdown_requested:
				try:
					data, addr = sock.recvfrom(1024)
					message = json.loads(data.decode())
					
					if message.get('type') == 'discovery_request':
						# Respond with node information
						response = {
							'type': 'discovery_response',
							'node_name': node_name,
							'ip': self._get_local_ip(),
							'port': self.communication_port,
							'blender_version': bpy.app.version_string,
							'plugin_version': bl_info['version'],
							'requires_auth': requires_auth,
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
				except:
					pass
	
	def _communication_server_loop(self):
		"""Communication server main loop for handling connections"""
		server_sock = None
		try:
			server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			server_sock.bind(('', self.communication_port))
			server_sock.listen(5)
			server_sock.settimeout(1.0)
			
			while self.communication_active and not self._shutdown_requested:
				try:
					client_sock, addr = server_sock.accept()
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
				except:
					pass
	
	def _handle_client(self, client_sock, addr):
		"""Handle individual client connections"""
		try:
			client_sock.settimeout(30.0)
			
			while not self._shutdown_requested:
				try:
					length_data = client_sock.recv(4)
					if not length_data:
						break
					
					message_length = struct.unpack('!I', length_data)[0]
					
					# Receive full message
					message_data = b''
					bytes_received = 0
					while bytes_received < message_length:
						chunk = client_sock.recv(min(message_length - bytes_received, 4096))
						if not chunk:
							break
						message_data += chunk
						bytes_received += len(chunk)
					
					if bytes_received != message_length:
						break
					
					message = json.loads(message_data.decode())
					response = self._process_message(message, addr, client_sock)
					
					# Send response
					response_data = json.dumps(response).encode()
					client_sock.send(struct.pack('!I', len(response_data)))
					client_sock.sendall(response_data)
				
				except json.JSONDecodeError:
					error_response = {'status': 'error', 'message': 'Invalid JSON'}
					response_data = json.dumps(error_response).encode()
					client_sock.send(struct.pack('!I', len(response_data)))
					client_sock.sendall(response_data)
					break
		
		except Exception as e:
			print(f"Client handler error: {e}")
		finally:
			try:
				client_sock.close()
			except:
				pass
	
	def _process_message(self, message, addr, client_sock):
		"""Process incoming messages from clients"""
		msg_type = message.get('type')
		
		if msg_type == 'connection_test':
			auth_token = message.get('auth_token')
			
			if not self.stored_password_hash:
				return {'status': 'success', 'message': 'Connection successful'}
			
			if auth_token and auth_token in self.security.auth_tokens:
				return {'status': 'success', 'message': 'Connection successful'}
			else:
				return {'status': 'error', 'message': 'Authentication required'}
			
		elif msg_type == 'authenticate':
			password = message.get('password', '')
			
			if not self.stored_password_hash:
				return {'status': 'error', 'message': 'No authentication required'}
			
			if self.security.verify_password(password, self.stored_password_hash, self.stored_salt):
				auth_token = self.security.generate_auth_token()
				self.security.auth_tokens[auth_token] = {
					'created': time.time(),
					'ip': addr[0]
				}
				return {'status': 'success', 'auth_token': auth_token}
			else:
				return {'status': 'error', 'message': 'Invalid password'}
			
		elif msg_type == 'get_project_manifest':
			return self._handle_get_manifest(message)
			
		elif msg_type == 'sync_file':
			return self._handle_sync_file(message, client_sock)
			
		elif msg_type == 'render_request':
			return self._handle_render_request(message, addr)
			
		elif msg_type == 'render_status':
			return self._handle_render_status_request(message)
			
		elif msg_type == 'render_cancel':
			return self._handle_render_cancel(message)
			
		elif msg_type == 'output_file_sync':
			return self._handle_output_file_sync(message, client_sock)
		
		else:
			return {'status': 'error', 'message': 'Unknown message type'}
	
	def _handle_get_manifest(self, message):
		"""Handle request for project manifest"""
		try:
			auth_token = message.get('auth_token')
			
			if self.stored_password_hash:
				if not auth_token or auth_token not in self.security.auth_tokens:
					return {'status': 'error', 'message': 'Authentication required'}
			
			prefs = bpy.context.preferences.addons[__package__].preferences
			cache_dir = bpy.path.abspath(prefs.remote_cache_directory)
			project_name = message.get('project_name', 'default')
			
			project_cache_dir = os.path.join(cache_dir, project_name)
			
			if os.path.exists(project_cache_dir):
				manifest = file_sync_manager.get_directory_manifest(project_cache_dir)
				return {'status': 'success', 'manifest': manifest}
			else:
				return {'status': 'success', 'manifest': {}}
				
		except Exception as e:
			print(f"Manifest request failed: {e}")
			return {'status': 'error', 'message': f'Failed to get manifest: {e}'}
	
	def _handle_sync_file(self, message, client_sock):
		"""Handle file synchronization request"""
		try:
			auth_token = message.get('auth_token')
			
			if self.stored_password_hash:
				if not auth_token or auth_token not in self.security.auth_tokens:
					return {'status': 'error', 'message': 'Authentication required'}
			
			prefs = bpy.context.preferences.addons[__package__].preferences
			cache_dir = bpy.path.abspath(prefs.remote_cache_directory)
			project_name = message.get('project_name', 'default')
			file_path = message.get('file_path')
			file_size = message.get('file_size', 0)
			
			if not file_path:
				return {'status': 'error', 'message': 'File path required'}
			
			# Validate file path (security check)
			if '..' in file_path or file_path.startswith('/'):
				return {'status': 'error', 'message': 'Invalid file path'}
			
			project_cache_dir = os.path.join(cache_dir, project_name)
			target_file_path = os.path.join(project_cache_dir, file_path)
			
			# Create directory if needed
			os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
			
			# Receive file data
			bytes_received = 0
			with open(target_file_path, 'wb') as f:
				while bytes_received < file_size:
					chunk_size = min(file_sync_manager.chunk_size, file_size - bytes_received)
					chunk = client_sock.recv(chunk_size)
					if not chunk:
						break
					f.write(chunk)
					bytes_received += len(chunk)
			
			if bytes_received == file_size:
				return {'status': 'success', 'message': 'File received'}
			else:
				return {'status': 'error', 'message': 'Incomplete file transfer'}
				
		except Exception as e:
			print(f"File sync failed: {e}")
			return {'status': 'error', 'message': f'File sync failed: {e}'}
			
	def _handle_render_request(self, message, addr):
		"""Handle render request from source computer"""
		try:
			auth_token = message.get('auth_token')
			
			if self.stored_password_hash:
				if not auth_token or auth_token not in self.security.auth_tokens:
					return {'status': 'error', 'message': 'Authentication required'}
			
			render_settings = message.get('render_settings', {})
			project_name = message.get('project_name', 'default')
			blend_file = message.get('blend_file')
			
			if not blend_file:
				return {'status': 'error', 'message': 'Blend file path required'}
			
			# Set up output sync callback
			global output_sync_manager
			if not output_sync_manager:
				output_sync_manager = OutputSyncManager(self)
			
			def output_callback(output_file, frame_number):
				output_sync_manager.queue_output_file(
					output_file,
					frame_number,
					addr[0],
					self.communication_port,
					auth_token,
					project_name
				)
			
			# Start render - mark as rendering to prevent connection drops
			self.is_rendering = True
			result = render_manager.start_render(blend_file, render_settings, output_callback)
			return result
			
		except Exception as e:
			print(f"Render request failed: {e}")
			self.is_rendering = False
			return {'status': 'error', 'message': f'Render request failed: {e}'}
			
	def _handle_render_status_request(self, message):
		"""Handle request for render status"""
		try:
			status = render_manager.get_render_status()
			return {'status': 'success', 'render_status': status}
		except Exception as e:
			return {'status': 'error', 'message': f'Status request failed: {e}'}
			
	def _handle_render_cancel(self, message):
		"""Handle render cancellation request"""
		try:
			render_manager.cancel_render()
			self.is_rendering = False
			return {'status': 'success', 'message': 'Render cancelled'}
		except Exception as e:
			return {'status': 'error', 'message': f'Cancel request failed: {e}'}
	
	def _handle_output_file_sync(self, message, client_sock):
		"""Handle output file synchronization from target to source"""
		try:
			file_path = message.get('file_path')
			file_size = message.get('file_size', 0)
			frame_number = message.get('frame_number', 0)
			
			if not file_path:
				return {'status': 'error', 'message': 'File path required'}
			
			# Create output directory
			blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.getcwd()
			output_dir = os.path.join(blend_dir, 'output')
			os.makedirs(output_dir, exist_ok=True)
			
			filename = os.path.basename(file_path)
			target_path = os.path.join(output_dir, filename)
			
			# Receive file data
			bytes_received = 0
			with open(target_path, 'wb') as f:
				while bytes_received < file_size:
					chunk_size = min(file_sync_manager.chunk_size, file_size - bytes_received)
					chunk = client_sock.recv(chunk_size)
					if not chunk:
						break
					f.write(chunk)
					bytes_received += len(chunk)
			
			if bytes_received == file_size:
				return {'status': 'success', 'message': 'Output file received'}
			else:
				return {'status': 'error', 'message': 'Incomplete file transfer'}
				
		except Exception as e:
			return {'status': 'error', 'message': f'Output file sync failed: {e}'}
	
	def discover_nodes(self, timeout=3):
		"""Discover available nodes on the network"""
		discovered = {}
		
		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
			sock.settimeout(0.5)
			
			request = {
				'type': 'discovery_request',
				'timestamp': time.time()
			}
			
			request_data = json.dumps(request).encode()
			
			# Broadcast to common network ranges
			broadcast_addresses = [
				'255.255.255.255',
				'192.168.1.255',
				'192.168.0.255',
				'10.0.0.255'
			]
			
			for broadcast_addr in broadcast_addresses:
				try:
					sock.sendto(request_data, (broadcast_addr, self.discovery_port))
				except:
					continue
				
			# Collect responses
			start_time = time.time()
			while time.time() - start_time < timeout:
				try:
					data, addr = sock.recvfrom(1024)
					response = json.loads(data.decode())
					
					if response.get('type') == 'discovery_response':
						node_id = f"{response['ip']}:{response['port']}"
						discovered[node_id] = {
							'name': response['node_name'],
							'ip': response['ip'],
							'port': response['port'],
							'blender_version': response['blender_version'],
							'plugin_version': response['plugin_version'],
							'requires_auth': response['requires_auth'],
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
			except:
				pass
				
		self.discovered_nodes = discovered
		return discovered
	
	def test_connection(self, ip, port, auth_token=None):
		"""Test connection to a remote node"""
		try:
			with socket.create_connection((ip, port), timeout=5) as sock:
				test_message = {
					'type': 'connection_test',
					'auth_token': auth_token,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(test_message).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return False
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				return response.get('status') == 'success'
			
		except Exception as e:
			print(f"Connection test failed: {e}")
			return False
	
	def authenticate(self, ip, port, password):
		"""Authenticate with a remote node"""
		try:
			with socket.create_connection((ip, port), timeout=5) as sock:
				auth_message = {
					'type': 'authenticate',
					'password': password,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(auth_message).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				if response.get('status') == 'success':
					return response.get('auth_token')
					
		except Exception as e:
			print(f"Authentication failed: {e}")
			
		return None
	
	def get_remote_manifest(self, ip, port, auth_token, project_name):
		"""Get project manifest from remote node"""
		try:
			with socket.create_connection((ip, port), timeout=10) as sock:
				request = {
					'type': 'get_project_manifest',
					'auth_token': auth_token,
					'project_name': project_name,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				
				response_data = b''
				bytes_received = 0
				while bytes_received < response_length:
					chunk = sock.recv(min(response_length - bytes_received, 4096))
					if not chunk:
						break
					response_data += chunk
					bytes_received += len(chunk)
				
				response = json.loads(response_data.decode())
				
				if response.get('status') == 'success':
					return response.get('manifest', {})
					
		except Exception as e:
			print(f"Failed to get remote manifest: {e}")
			
		return None
	
	def sync_file_to_remote(self, ip, port, auth_token, project_name, file_path, local_file_path):
		"""Sync a file to remote node"""
		try:
			file_size = os.path.getsize(local_file_path)
			
			with socket.create_connection((ip, port), timeout=30) as sock:
				request = {
					'type': 'sync_file',
					'auth_token': auth_token,
					'project_name': project_name,
					'file_path': file_path,
					'file_size': file_size,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				# Send file data
				with open(local_file_path, 'rb') as f:
					bytes_sent = 0
					while bytes_sent < file_size:
						chunk = f.read(file_sync_manager.chunk_size)
						if not chunk:
							break
						sock.sendall(chunk)
						bytes_sent += len(chunk)
				
				# Receive response
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return False
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				return response.get('status') == 'success'
				
		except Exception as e:
			print(f"File sync failed: {e}")
			return False
	
	def sync_output_file_to_source(self, ip, port, auth_token, file_path, frame_number):
		"""Sync output file back to source computer"""
		try:
			if not os.path.exists(file_path):
				return False
				
			file_size = os.path.getsize(file_path)
			
			with socket.create_connection((ip, port), timeout=30) as sock:
				request = {
					'type': 'output_file_sync',
					'auth_token': auth_token,
					'file_path': file_path,
					'file_size': file_size,
					'frame_number': frame_number,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				# Send file data
				with open(file_path, 'rb') as f:
					bytes_sent = 0
					while bytes_sent < file_size:
						chunk = f.read(file_sync_manager.chunk_size)
						if not chunk:
							break
						sock.sendall(chunk)
						bytes_sent += len(chunk)
				
				# Receive response
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return False
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				return response.get('status') == 'success'
				
		except Exception as e:
			print(f"Output file sync failed: {e}")
			return False
	
	def _get_local_ip(self):
		"""Get local IP address"""
		try:
			with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
				s.connect(("8.8.8.8", 80))
				return s.getsockname()[0]
		except:
			return "127.0.0.1"
			
	def send_render_request(self, ip, port, auth_token, project_name, blend_file, render_settings):
		"""Send render request to remote node"""
		try:
			with socket.create_connection((ip, port), timeout=30) as sock:
				request = {
					'type': 'render_request',
					'auth_token': auth_token,
					'project_name': project_name,
					'blend_file': blend_file,
					'render_settings': render_settings,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				# Receive response
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				return response
				
		except Exception as e:
			print(f"Render request failed: {e}")
			return None
			
	def get_render_status(self, ip, port, auth_token):
		"""Get render status from remote node"""
		try:
			with socket.create_connection((ip, port), timeout=10) as sock:
				request = {
					'type': 'render_status',
					'auth_token': auth_token,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				if response.get('status') == 'success':
					return response.get('render_status')
					
		except Exception as e:
			print(f"Render status request failed: {e}")
			
		return None
		
	def cancel_remote_render(self, ip, port, auth_token):
		"""Cancel render on remote node"""
		try:
			with socket.create_connection((ip, port), timeout=10) as sock:
				request = {
					'type': 'render_cancel',
					'auth_token': auth_token,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return False
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				return response.get('status') == 'success'
				
		except Exception as e:
			print(f"Render cancel failed: {e}")
			return False

# ----
# Rendering Management
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
		self.output_callback = None
		self._handlers_registered = False
		self.output_file_monitor = None
		
	def start_render(self, blend_file_path, render_settings, output_callback=None):
		"""Queue a render request"""
		if self.active_render and self.render_status in ['preparing', 'rendering']:
			return {'status': 'error', 'message': 'Render already in progress'}
		
		render_request = {
			'blend_file_path': blend_file_path,
			'render_settings': render_settings,
			'output_callback': output_callback,
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
				# Mark rendering as complete so connections can be cleaned up if needed
				network_manager.is_rendering = False
			
			return None
		
		timer_manager.register_timer(process_render, interval=0.1)
		
		return {'status': 'success', 'message': 'Render queued'}
	
	def _execute_render_request(self, render_request):
		"""Execute render request on main thread"""
		blend_file_path = render_request['blend_file_path']
		render_settings = render_request['render_settings']
		output_callback = render_request['output_callback']
		
		# Ensure blend file exists
		if not os.path.exists(blend_file_path):
			raise Exception(f'Blend file not found: {blend_file_path}')
		
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
		except Exception as e:
			raise Exception(f"Failed to load blend file: {e}")
		
		# Apply render settings
		self._apply_render_settings(render_settings)
		
		# Set up progress monitoring
		self._setup_render_monitoring(output_callback)
		
		# Set up output file monitoring for automatic sync
		self._setup_output_file_monitoring(render_settings)
		
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
		if 'output_path' in settings:
			scene.render.filepath = settings['output_path']
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
	
	def _setup_output_file_monitoring(self, render_settings):
		"""Set up monitoring for newly created files during rendering"""
		if not self.output_callback:
			return
			
		# Get output directory
		scene = bpy.context.scene
		output_path = bpy.path.abspath(scene.render.filepath)
		output_dir = os.path.dirname(output_path)
		
		# Create a file monitor that watches for new files
		self.output_file_monitor = OutputFileMonitor(output_dir, self.output_callback)
		self.output_file_monitor.start_monitoring()
			
	def _setup_render_monitoring(self, output_callback):
		"""Set up render progress monitoring"""
		self.output_callback = output_callback
		
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
				except:
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

class OutputFileMonitor:
	"""Monitors directory for new files created during rendering"""
	
	def __init__(self, output_dir, callback):
		self.output_dir = output_dir
		self.callback = callback
		self.monitoring = False
		self.monitor_thread = None
		self.known_files = set()
		
		# Initialize known files
		if os.path.exists(self.output_dir):
			for root, dirs, files in os.walk(self.output_dir):
				for file in files:
					file_path = os.path.join(root, file)
					self.known_files.add(file_path)
	
	def start_monitoring(self):
		"""Start monitoring for new files"""
		if self.monitoring:
			return
			
		self.monitoring = True
		self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
		self.monitor_thread.start()
	
	def stop_monitoring(self):
		"""Stop monitoring"""
		self.monitoring = False
		if self.monitor_thread:
			self.monitor_thread.join(timeout=2)
	
	def _monitor_loop(self):
		"""Main monitoring loop"""
		while self.monitoring:
			try:
				if os.path.exists(self.output_dir):
					current_files = set()
					
					for root, dirs, files in os.walk(self.output_dir):
						for file in files:
							file_path = os.path.join(root, file)
							current_files.add(file_path)
					
					# Find new files
					new_files = current_files - self.known_files
					
					for new_file in new_files:
						# Wait a moment to ensure file is completely written
						time.sleep(0.5)
						
						if os.path.exists(new_file) and self.callback:
							try:
								# Extract frame number from filename if possible
								frame_number = 0
								filename = os.path.basename(new_file)
								import re
								frame_match = re.search(r'(\d{4})', filename)
								if frame_match:
									frame_number = int(frame_match.group(1))
								
								self.callback(new_file, frame_number)
							except Exception as e:
								print(f"Error syncing new output file {new_file}: {e}")
					
					self.known_files = current_files
				
				time.sleep(1)  # Check every second
				
			except Exception as e:
				print(f"Output file monitor error: {e}")
				time.sleep(2)

class OutputSyncManager:
	"""Manages synchronization of rendered output files back to source"""
	
	def __init__(self, network_manager):
		self.network_manager = network_manager
		self.sync_queue = []
		self.sync_thread = None
		self.sync_active = False
		
	def queue_output_file(self, file_path, frame_number, source_ip, source_port, auth_token, project_name):
		"""Queue an output file for syncing back to source"""
		sync_item = {
			'file_path': file_path,
			'frame_number': frame_number,
			'source_ip': source_ip,
			'source_port': source_port,
			'auth_token': auth_token,
			'project_name': project_name,
			'timestamp': time.time()
		}
		
		self.sync_queue.append(sync_item)
		
		if not self.sync_active:
			self.start_sync_thread()
			
	def start_sync_thread(self):
		"""Start background thread for syncing output files"""
		if self.sync_active:
			return
			
		self.sync_active = True
		self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
		self.sync_thread.start()
		
	def stop_sync_thread(self):
		"""Stop sync thread"""
		self.sync_active = False
		if self.sync_thread:
			self.sync_thread.join(timeout=5)
			
	def _sync_loop(self):
		"""Main loop for output file synchronization"""
		while self.sync_active:
			if self.sync_queue:
				sync_item = self.sync_queue.pop(0)
				self._sync_output_file(sync_item)
			else:
				time.sleep(0.5)
				
	def _sync_output_file(self, sync_item):
		"""Sync individual output file back to source"""
		try:
			file_path = sync_item['file_path']
			
			if not os.path.exists(file_path):
				return
			
			# Retry logic for network connection issues
			max_retries = 3
			retry_delay = 2
			
			for attempt in range(max_retries):
				try:
					success = self.network_manager.sync_output_file_to_source(
						sync_item['source_ip'],
						sync_item['source_port'],
						sync_item['auth_token'],
						file_path,
						sync_item['frame_number']
					)
					
					if success:
						print(f"Successfully synced output file: {os.path.basename(file_path)}")
						return
						
				except Exception as e:
					print(f"Error syncing output file (attempt {attempt + 1}/{max_retries}): {e}")
				
				if attempt < max_retries - 1:
					time.sleep(retry_delay)
					retry_delay *= 2
				
		except Exception as e:
			print(f"Error syncing output file: {e}")

# Global instances
render_manager = RenderManager()
output_sync_manager = None
network_manager = NetworkManager()

# ----
# Property Groups for UI State
# ----

class SyncFileInfo(PropertyGroup):
	"""Information about a file that needs syncing"""
	file_path: StringProperty()
	status: StringProperty()  # 'new', 'modified', 'deleted'
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
		default='SOURCE'
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
# Operators
# ----

class REMOTERENDER_OT_StartDiscovery(Operator):
	bl_idname = "render_remote.start_discovery"
	bl_label = "Start Discovery"
	bl_description = "Start discovery server to allow other instances to find this computer"
	
	def execute(self, context):
		props = context.scene.remote_render_props
		prefs = context.preferences.addons[__package__].preferences
		
		if network_manager.discovery_active:
			self.report({'WARNING'}, "Discovery already active")
			return {'CANCELLED'}
		
		network_manager.update_ports_from_preferences()
		
		network_manager.start_discovery_server(
			props.node_name,
			prefs.remote_passcode
		)
		
		auth_status = "with authentication" if prefs.remote_passcode else "without authentication"
		self.report({'INFO'}, f"Discovery started for node: {props.node_name} ({auth_status})")
		return {'FINISHED'}

class REMOTERENDER_OT_StopDiscovery(Operator):
	bl_idname = "render_remote.stop_discovery"
	bl_label = "Stop Discovery"
	bl_description = "Stop discovery server"
	
	def execute(self, context):
		network_manager.stop_discovery_server()
		self.report({'INFO'}, "Discovery stopped")
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
		
		# Test connection
		auth_token = None
		if target_node.requires_auth:
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
			
			if not network_manager.communication_active:
				network_manager.start_communication_server()
			
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
		
		# Test connection
		auth_token = None
		if props.connection_password:
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
			manual_node.requires_auth = bool(props.connection_password)
			
			props.selected_node = manual_node.node_id
			
			if not network_manager.communication_active:
				network_manager.start_communication_server()
			
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
		
		self.report({'INFO'}, "Scanning project dependencies...")
		
		def scan_project():
			try:
				dependencies = file_sync_manager.scan_blend_dependencies()
				
				sync_changes = None
				context = bpy.context
				props = context.scene.remote_render_props
				
				if props.selected_node:
					target_node = None
					for node in context.scene.discovered_nodes:
						if node.node_id == props.selected_node and node.is_connected:
							target_node = node
							break
					
					if target_node:
						project_root = file_sync_manager.get_project_root()
						if project_root:
							# Use the new method that only includes referenced files
							local_manifest = file_sync_manager.get_referenced_files_manifest(project_root, dependencies)
							
							remote_manifest = network_manager.get_remote_manifest(
								target_node.ip,
								target_node.port,
								target_node.auth_token,
								props.project_name
							)
							
							if remote_manifest is not None:
								sync_changes = file_sync_manager.compare_manifests(local_manifest, remote_manifest)
				
				def update_ui():
					context = bpy.context
					props = context.scene.remote_render_props
					
					props.external_files_count = len(dependencies['external'])
					props.show_external_warning = len(dependencies['external']) > 0
					
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
						props.sync_status = f"{total_files} files need sync"
					else:
						props.sync_status = "Up to date"
					
					return None
				
				timer_manager.register_timer(update_ui, interval=0.1)
			
			except Exception as e:
				print(f"Project scan failed: {e}")
				
				def update_error():
					context = bpy.context
					props = context.scene.remote_render_props
					props.sync_status = f"Scan failed: {e}"
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
		
		# Find connected node
		target_node = None
		for node in context.scene.discovered_nodes:
			if node.node_id == props.selected_node and node.is_connected:
				target_node = node
				break
		
		if not target_node:
			self.report({'ERROR'}, "Remote node not connected")
			return {'CANCELLED'}
		
		# Get selected files
		selected_files = [f for f in context.scene.sync_files if f.selected and f.status != 'deleted']
		
		if not selected_files:
			self.report({'WARNING'}, "No files selected for sync")
			return {'CANCELLED'}
		
		self.report({'INFO'}, f"Syncing {len(selected_files)} files...")
		
		def sync_files():
			try:
				project_root = file_sync_manager.get_project_root()
				if not project_root:
					raise Exception("Could not determine project root")
				
				success_count = 0
				total_files = len(selected_files)
				
				for file_info in selected_files:
					local_file_path = os.path.join(project_root, file_info.file_path)
					
					if os.path.exists(local_file_path):
						success = network_manager.sync_file_to_remote(
							target_node.ip,
							target_node.port,
							target_node.auth_token,
							props.project_name,
							file_info.file_path,
							local_file_path
						)
						
						if success:
							success_count += 1
				
				def update_ui():
					context = bpy.context
					props = context.scene.remote_render_props
					props.sync_status = f"Synced {success_count}/{total_files} files"
					
					if success_count > 0:
						bpy.ops.render_remote.scan_project()
					
					return None
				
				timer_manager.register_timer(update_ui, interval=0.1)
			
			except Exception as e:
				print(f"File sync failed: {e}")
				
				def update_error():
					context = bpy.context
					props = context.scene.remote_render_props
					props.sync_status = f"Sync failed: {e}"
					return None
				
				timer_manager.register_timer(update_error, interval=0.1)
		
		threading.Thread(target=sync_files, daemon=True).start()
		
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

class REMOTERENDER_OT_StartRemoteRender(Operator):
	bl_idname = "render_remote.start_remote_render"
	bl_label = "Start Remote Render"
	bl_description = "Start rendering on remote computer"
	
	animation: BoolProperty(name="Animation", default=False)
	
	def execute(self, context):
		props = context.scene.remote_render_props
		scene = context.scene
		
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
		
		# Prepare render settings
		render_settings = {
			'animation': self.animation,
			'frame_start': scene.frame_start,
			'frame_end': scene.frame_end,
			'frame_current': scene.frame_current,
			'output_path': scene.render.filepath,
			'file_format': scene.render.image_settings.file_format,
			'resolution_x': scene.render.resolution_x,
			'resolution_y': scene.render.resolution_y,
			'resolution_percentage': scene.render.resolution_percentage,
			'engine': scene.render.engine
		}
		
		# Get blend file path on remote
		prefs = context.preferences.addons[__package__].preferences
		cache_dir = bpy.path.abspath(prefs.remote_cache_directory)
		project_root = file_sync_manager.get_project_root()
		
		if not project_root:
			self.report({'ERROR'}, "Could not determine project root")
			return {'CANCELLED'}
		
		# Relative path of blend file
		blend_file_rel = os.path.relpath(bpy.data.filepath, project_root)
		remote_blend_path = os.path.join(cache_dir, props.project_name, blend_file_rel).replace('\\', '/')
		
		# Send render request
		result = network_manager.send_render_request(
			target_node.ip,
			target_node.port,
			target_node.auth_token,
			props.project_name,
			remote_blend_path,
			render_settings
		)
		
		if result and result.get('status') == 'success':
			self.report({'INFO'}, "Render started on remote computer")
			props.render_status = "Starting"
			props.monitor_render = True
			
			# Start progress monitoring
			self._start_progress_monitoring(context, target_node)
		else:
			error_msg = result.get('message', 'Unknown error') if result else 'Connection failed'
			self.report({'ERROR'}, f"Failed to start render: {error_msg}")
		
		return {'FINISHED'}
	
	def _start_progress_monitoring(self, context, target_node):
		"""Start monitoring render progress"""
		def monitor_progress():
			props = context.scene.remote_render_props
			
			if not props.monitor_render:
				return None
			
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
				props.render_error_message = status.get('error_message', '')
				
				if props.render_status in ['preparing', 'rendering']:
					return 2.0
				else:
					props.monitor_render = False
					return None
			else:
				props.render_status = "Connection Error"
				props.monitor_render = False
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
			props.render_status = "Cancelled"
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
			props.render_error_message = status.get('error_message', '')
			
			self.report({'INFO'}, f"Status: {props.render_status}")
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
#	bl_options = {'DEFAULT_CLOSED'}
	bl_order = 64
	
	@classmethod
	def poll(cls, context):
		try:
			return context.preferences.addons[__package__].preferences.remote_enable
		except:
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
		box.label(text="Target Mode - Allow Remote Rendering:", icon='NETWORK_DRIVE')
		
		col = box.column()
		col.prop(props, "node_name")
		
		# Show authentication status from preferences
		if prefs.remote_passcode:
			col.label(text="Authentication: Enabled", icon='LOCKED')
		else:
			col.label(text="Authentication: Disabled", icon='UNLOCKED')
		col.label(text="(Configure passcode in Add-on Preferences)", icon='PREFERENCES')
		
		# Discovery controls
		row = box.row()
		if network_manager.discovery_active:
			row.operator("render_remote.stop_discovery", icon='PAUSE')
			row.label(text="Active", icon='CHECKMARK')
		else:
			row.operator("render_remote.start_discovery", icon='PLAY')
			row.label(text="Inactive", icon='X')
	
	def draw_source_mode(self, layout, props, prefs):
		"""Draw UI for Source mode"""
		context = bpy.context
		box = layout.box()
		box.label(text="Source Mode - Control Remote Rendering:", icon='DESKTOP')
		
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
		if props.selected_node:
			box.separator()
			box.label(text=f"Active Connection: {props.selected_node}", icon='LINKED')
		
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
		box.label(text="File Synchronization:", icon='FILE_REFRESH')
		
		# Scan button and status
		row = box.row()
		row.operator("render_remote.scan_project", icon='VIEWZOOM')
		row.label(text=f"Status: {props.sync_status}")
		
		# External files warning
		if props.show_external_warning:
			warning_box = box.box()
			warning_box.alert = True
			warning_box.label(text=f"Warning: {props.external_files_count} external files detected!", icon='ERROR')
			warning_box.label(text="External files will NOT be synced to target computer.")
			warning_box.label(text="Only files within the project folder structure are supported.")
		
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
		if props.selected_node:
			layout.separator()
			self.draw_render_interface(layout, context, props)
	
	def draw_render_interface(self, layout, context, props):
		"""Draw render management interface"""
		box = layout.box()
		box.label(text="Remote Rendering:", icon='RENDER_ANIMATION')
		
		# Render controls
		if props.render_status in ['preparing', 'rendering']:
			# Render in progress
			row = box.row()
			row.operator("render_remote.cancel_remote_render", icon='X')
			row.operator("render_remote.refresh_render_status", icon='FILE_REFRESH')
			
			# Progress display
			if props.render_progress > 0:
				progress_box = box.box()
				progress_box.label(text=f"Status: {props.render_status}")
				
				# Progress bar
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
				
				if props.render_error_message:
					error_box = progress_box.box()
					error_box.alert = True
					error_box.label(text=f"Error: {props.render_error_message}", icon='ERROR')
		else:
			# Render controls
			row = box.row()
			
			# Animation render
			col = row.column()
			op = col.operator("render_remote.start_remote_render", text="Render Animation", icon='RENDER_ANIMATION')
			op.animation = True
			
			# Status refresh
			row = box.row()
			row.operator("render_remote.refresh_render_status", icon='FILE_REFRESH')
			
			# Show last status if available
			if props.render_status and props.render_status != "Not Started":
				status_box = box.box()
				status_box.label(text=f"Last Status: {props.render_status}")
				
				if props.render_error_message:
					status_box.label(text=f"Error: {props.render_error_message}", icon='ERROR')
		
		# Output monitoring
		box.separator()
		box.label(text="Output Files:")
		box.label(text="Rendered files will be automatically synced back to:")
		box.label(text=f"  {bpy.path.abspath('//output/')}")
		
		# Create output directory info
		output_dir = bpy.path.abspath('//output/')
		if not os.path.exists(output_dir):
			box.label(text="(Output directory will be created automatically)", icon='INFO')
		else:
			box.label(text="(Output directory exists)", icon='CHECKMARK')

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
	render_manager._clear_render_handlers()
	
	# Stop output file monitoring
	if render_manager.output_file_monitor:
		render_manager.output_file_monitor.stop_monitoring()
		render_manager.output_file_monitor = None
	
	# Mark rendering as complete so connections can be cleaned up if needed
	network_manager.is_rendering = False
	print("All rendering completed")

@persistent
def _render_write_handler(scene, depsgraph):
	"""Called when frame is written to disk"""
	global render_manager
	
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
	global network_manager, render_manager, timer_manager
	
	print("Cleaning up remote render resources")
	
	try:
		if network_manager:
			network_manager.stop_discovery_server()
	except Exception as e:
		print(f"Error stopping network manager: {e}")
	
	try:
		if render_manager:
			render_manager.cleanup()
	except Exception as e:
		print(f"Error cleaning up render manager: {e}")
	
	try:
		if timer_manager:
			timer_manager.cleanup_all()
	except Exception as e:
		print(f"Error cleaning up timer manager: {e}")

@persistent
def cleanup_on_load_pre(dummy):
	"""Clean up before loading files"""
	global render_manager, timer_manager
	
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
	except Exception as e:
		print(f"Error resetting connection status: {e}")

# ----
# Registration
# ----

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

def register():
	# Register all classes
	for cls in classes:
		bpy.utils.register_class(cls)
		
	# Register property groups
	bpy.types.Scene.remote_render_props = bpy.props.PointerProperty(type=RemoteRenderProperties)
	bpy.types.Scene.discovered_nodes = bpy.props.CollectionProperty(type=RemoteNodeProperties)
	bpy.types.Scene.sync_files = bpy.props.CollectionProperty(type=SyncFileInfo)
	
	# Register cleanup handlers
	if cleanup_on_exit not in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.append(cleanup_on_exit)
	
	if cleanup_on_load_pre not in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.append(cleanup_on_load_pre)
	
	if reset_connection_status_on_load not in bpy.app.handlers.load_post:
		bpy.app.handlers.load_post.append(reset_connection_status_on_load)
	
	print("Remote Render Sync add-on registered")

def unregister():
	# Cleanup network manager
	global network_manager, render_manager, timer_manager
	
	if network_manager:
		network_manager.stop_discovery_server()
	
	# Cleanup render manager
	if render_manager:
		render_manager.cleanup()
	
	# Cleanup timer manager
	if timer_manager:
		timer_manager.cleanup_all()
	
	# Remove handlers
	if cleanup_on_exit in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.remove(cleanup_on_exit)
		
	if cleanup_on_load_pre in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.remove(cleanup_on_load_pre)
	
	if reset_connection_status_on_load in bpy.app.handlers.load_post:
		bpy.app.handlers.load_post.remove(reset_connection_status_on_load)
		
	# Unregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)
		
	# Remove properties
	if hasattr(bpy.types.Scene, 'remote_render_props'):
		del bpy.types.Scene.remote_render_props
	if hasattr(bpy.types.Scene, 'discovered_nodes'):
		del bpy.types.Scene.discovered_nodes
	if hasattr(bpy.types.Scene, 'sync_files'):
		del bpy.types.Scene.sync_files
	
	print("Remote Render Sync add-on unregistered")