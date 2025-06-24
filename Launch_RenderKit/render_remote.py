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
					
		# Categorize files
		for file_path in file_paths:
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
		if is_server:
			context.check_hostname = False
			context.verify_mode = ssl.CERT_NONE
		else:
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
		self.discovery_port = 5001 # Port preferences will be updated later when available
		self.communication_port = 5002 # Port preferences will be updated later when available
		self.broadcast_interval = 5
		self.discovery_active = False
		self.communication_active = False
		self.discovered_nodes = {}
		self.discovery_thread = None
		self.communication_thread = None
		self.security = SecureConnection()
		self.stored_password_hash = None
		self.stored_salt = None
		self.preserve_network_on_file_load = False  # Flag to prevent shutdown during file loads
	
	def update_ports_from_preferences(self):
		"""Update ports from addon preferences if available"""
		try:
			prefs = bpy.context.preferences.addons[__package__].preferences
			self.discovery_port = prefs.remote_discovery_port
			self.communication_port = prefs.remote_communication_port
		except (AttributeError, KeyError):
			# Keep existing values if preferences aren't available
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
		self.discovery_active = False
		if self.discovery_thread:
			self.discovery_thread.join(timeout=1)
			
		self.stop_communication_server()
		print("Discovery server stopped")
	
	def start_communication_server(self):
		"""Start communication server for handling connections"""
		if self.communication_active:
			return
		
		# Ensure we have the latest port values from preferences
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
		self.communication_active = False
		if self.communication_thread:
			self.communication_thread.join(timeout=1)
		print("Communication server stopped")
	
	def _discovery_server_loop(self, node_name, requires_auth):
		"""Discovery server main loop"""
		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			sock.bind(('', self.discovery_port))
			sock.settimeout(1.0)
			
			while self.discovery_active:
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
					print(f"Discovery server error: {e}")
					
		except Exception as e:
			print(f"Failed to start discovery server: {e}")
		finally:
			try:
				sock.close()
			except:
				pass
	
	def _communication_server_loop(self):
		"""Communication server main loop for handling connections"""
		try:
			# Create server socket
			server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			server_sock.bind(('', self.communication_port))
			server_sock.listen(5)
			server_sock.settimeout(1.0)
			
			print(f"Communication server listening on port {self.communication_port}")
			
			while self.communication_active:
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
					if self.communication_active:  # Only log if we're supposed to be active
						print(f"Communication server error: {e}")
					
		except Exception as e:
			print(f"Failed to start communication server: {e}")
		finally:
			try:
				server_sock.close()
			except:
				pass
	
	def _handle_client(self, client_sock, addr):
		"""Handle individual client connections"""
		try:
			client_sock.settimeout(30.0)  # Longer timeout for file transfers
			
			while True:
				# Receive message length first
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
			print(f"Connection test from {addr[0]}, auth_token: {'provided' if auth_token else 'missing'}")
			
			# If no auth required, allow connection
			if not self.stored_password_hash:
				print("No authentication required - connection allowed")
				return {'status': 'success', 'message': 'Connection successful'}
			
			# If auth required, check token
			if auth_token and auth_token in self.security.auth_tokens:
				print(f"Valid auth token found for connection test")
				return {'status': 'success', 'message': 'Connection successful'}
			else:
				print(f"Authentication required for connection test. Token valid: {auth_token in self.security.auth_tokens if auth_token else False}")
				return {'status': 'error', 'message': 'Authentication required'}
			
		elif msg_type == 'authenticate':
			password = message.get('password', '')
			print(f"Authentication request from {addr[0]}")
			
			# If no password set, reject
			if not self.stored_password_hash:
				print("Authentication rejected - no password configured")
				return {'status': 'error', 'message': 'No authentication required'}
			
			# Verify password
			if self.security.verify_password(password, self.stored_password_hash, self.stored_salt):
				# Generate and store auth token
				auth_token = self.security.generate_auth_token()
				self.security.auth_tokens[auth_token] = {
					'created': time.time(),
					'ip': addr[0]
				}
				print(f"Authentication successful for {addr[0]}, token generated. Total active tokens: {len(self.security.auth_tokens)}")
				return {'status': 'success', 'auth_token': auth_token}
			else:
				print(f"Authentication failed for {addr[0]} - invalid password")
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
			print(f"Manifest request, auth_token: {'provided' if auth_token else 'missing'}")
			
			# Check authentication if required
			if self.stored_password_hash:
				if not auth_token or auth_token not in self.security.auth_tokens:
					print("Manifest request rejected - authentication required")
					return {'status': 'error', 'message': 'Authentication required'}
			
			prefs = bpy.context.preferences.addons[__package__].preferences
			cache_dir = bpy.path.abspath(prefs.remote_cache_directory)
			project_name = message.get('project_name', 'default')
			
			project_cache_dir = os.path.join(cache_dir, project_name)
			
			if os.path.exists(project_cache_dir):
				manifest = file_sync_manager.get_directory_manifest(project_cache_dir)
				print(f"Manifest generated with {len(manifest)} files")
				return {'status': 'success', 'manifest': manifest}
			else:
				print("No project cache directory found")
				return {'status': 'success', 'manifest': {}}
				
		except Exception as e:
			print(f"Manifest request failed: {e}")
			return {'status': 'error', 'message': f'Failed to get manifest: {e}'}
	
	def _handle_sync_file(self, message, client_sock):
		"""Handle file synchronization request"""
		try:
			auth_token = message.get('auth_token')
			print(f"File sync request, auth_token: {'provided' if auth_token else 'missing'}")
			
			# Check authentication if required
			if self.stored_password_hash:
				if not auth_token or auth_token not in self.security.auth_tokens:
					print("File sync request rejected - authentication required")
					return {'status': 'error', 'message': 'Authentication required'}
			
			prefs = bpy.context.preferences.addons[__package__].preferences
			cache_dir = bpy.path.abspath(prefs.remote_cache_directory)
			project_name = message.get('project_name', 'default')
			file_path = message.get('file_path')
			file_size = message.get('file_size', 0)
			
			print(f"Syncing file: {file_path} ({file_size} bytes)")
			
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
				print(f"File sync successful: {file_path}")
				return {'status': 'success', 'message': 'File received'}
			else:
				print(f"File sync incomplete: {bytes_received}/{file_size} bytes")
				return {'status': 'error', 'message': 'Incomplete file transfer'}
				
		except Exception as e:
			print(f"File sync failed: {e}")
			return {'status': 'error', 'message': f'File sync failed: {e}'}
			
	def _handle_render_request(self, message, addr):
		"""Handle render request from source computer"""
		try:
			# Check authentication if required
			auth_token = message.get('auth_token')
			print(f"Render request from {addr[0]}, auth_token: {'provided' if auth_token else 'missing'}")
			
			if self.stored_password_hash:
				if not auth_token:
					print("Authentication required but no token provided")
					return {'status': 'error', 'message': 'Authentication required - no token'}
				
				# Clean up expired tokens (older than 1 hour)
				current_time = time.time()
				expired_tokens = []
				for token, token_info in self.security.auth_tokens.items():
					if current_time - token_info['created'] > 3600:  # 1 hour
						expired_tokens.append(token)
				
				for token in expired_tokens:
					del self.security.auth_tokens[token]
				
				if auth_token not in self.security.auth_tokens:
					print(f"Invalid or expired auth token. Active tokens: {len(self.security.auth_tokens)}")
					return {'status': 'error', 'message': 'Authentication required - invalid token'}
				
				# Verify the token is from the correct IP
				token_info = self.security.auth_tokens[auth_token]
				if token_info['ip'] != addr[0]:
					print(f"Token IP mismatch: expected {token_info['ip']}, got {addr[0]}")
					return {'status': 'error', 'message': 'Authentication required - IP mismatch'}
				
				print(f"Authentication successful for {addr[0]}")
			else:
				print("No authentication required")
			
			# Get render settings
			render_settings = message.get('render_settings', {})
			project_name = message.get('project_name', 'default')
			blend_file = message.get('blend_file')
			
			if not blend_file:
				return {'status': 'error', 'message': 'Blend file path required'}
			
			print(f"Starting render for project: {project_name}, blend file: {blend_file}")
			
			# Set up output sync callback
			global output_sync_manager
			if not output_sync_manager:
				output_sync_manager = OutputSyncManager(self)
			
			def output_callback(output_file, frame_number):
				output_sync_manager.queue_output_file(
					output_file,
					frame_number,
					addr[0],  # source IP
					self.communication_port,
					auth_token,
					project_name
				)
			
			# Start render
			result = render_manager.start_render(blend_file, render_settings, output_callback)
			print(f"Render start result: {result}")
			return result
			
		except Exception as e:
			print(f"Render request failed with exception: {e}")
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
			return {'status': 'success', 'message': 'Render cancelled'}
		except Exception as e:
			return {'status': 'error', 'message': f'Cancel request failed: {e}'}
	
	def _handle_output_file_sync(self, message, client_sock):
		"""Handle output file synchronization from target to source"""
		try:
			# This is called on the source computer to receive output files
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
				print(f"Received output file: {filename}")
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
			
			# Send discovery request
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
				# Send connection test
				test_message = {
					'type': 'connection_test',
					'auth_token': auth_token,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(test_message).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				# Receive response length
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return False
					
				response_length = struct.unpack('!I', length_data)[0]
				
				# Receive response
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
				# Send authentication request
				auth_message = {
					'type': 'authenticate',
					'password': password,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(auth_message).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				# Receive response length
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				
				# Receive response
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				if response.get('status') == 'success':
					return response.get('auth_token')
				else:
					print(f"Authentication error: {response.get('message', 'Unknown error')}")
					
		except Exception as e:
			print(f"Authentication failed: {e}")
			
		return None
	
	def get_remote_manifest(self, ip, port, auth_token, project_name):
		"""Get project manifest from remote node"""
		try:
			with socket.create_connection((ip, port), timeout=10) as sock:
				# Send manifest request
				request = {
					'type': 'get_project_manifest',
					'auth_token': auth_token,
					'project_name': project_name,
					'timestamp': time.time()
				}
				
				message_data = json.dumps(request).encode()
				sock.send(struct.pack('!I', len(message_data)))
				sock.sendall(message_data)
				
				# Receive response length
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				
				# Receive response
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
				# Send sync request
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
				# Send output file sync request
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
			# Connect to a remote address to determine local IP
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
				
				# Receive response
				length_data = sock.recv(4)
				if len(length_data) != 4:
					return None
					
				response_length = struct.unpack('!I', length_data)[0]
				response_data = sock.recv(response_length)
				response = json.loads(response_data.decode())
				
				if response.get('status') == 'success':
					return response.get('render_status')
				else:
					print(f"Render status error: {response.get('message')}")
					
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
				
				# Receive response
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
		self.temp_render_handler = None
		self.original_output_path = ""
		self.render_queue = []
		self.queue_timer_active = False
		
	def start_render(self, blend_file_path, render_settings, output_callback=None):
		"""Queue a render request (called from network thread)"""
		if self.active_render and self.render_status in ['preparing', 'rendering']:
			return {'status': 'error', 'message': 'Render already in progress'}
		
		# Queue the render request instead of executing immediately
		render_request = {
			'blend_file_path': blend_file_path,
			'render_settings': render_settings,
			'output_callback': output_callback,
			'timestamp': time.time()
		}
		
		self.render_queue.append(render_request)
		
		# Start processing queue if not already active
		if not self.queue_timer_active:
			self._start_queue_processor()
			
		return {'status': 'success', 'message': 'Render queued'}
	
	def _start_queue_processor(self):
		"""Start timer to process render queue on main thread"""
		if self.queue_timer_active:
			return
			
		self.queue_timer_active = True
		
		def process_queue():
			if not self.render_queue:
				self.queue_timer_active = False
				return None  # Stop timer
				
			# Process next render request
			render_request = self.render_queue.pop(0)
			
			try:
				self._execute_render_request(render_request)
			except Exception as e:
				print(f"Render execution failed: {e}")
				self.render_status = "error"
				self.render_error_message = str(e)
				self.active_render = False
			
			self.queue_timer_active = False
			return None  # Stop timer
		
		# Schedule on main thread
		bpy.app.timers.register(process_queue, first_interval=0.1)
	
	def _execute_render_request(self, render_request):
		"""Execute render request on main thread"""
		blend_file_path = render_request['blend_file_path']
		render_settings = render_request['render_settings']
		output_callback = render_request['output_callback']
		
		# Ensure blend file exists
		if not os.path.exists(blend_file_path):
			raise Exception(f'Blend file not found: {blend_file_path}')
		
		# Set flag to preserve network services during file load
		global network_manager
		network_manager.preserve_network_on_file_load = True
		
		try:
			# Load blend file (now safe on main thread)
			print(f"Loading blend file: {blend_file_path}")
			bpy.ops.wm.open_mainfile(filepath=blend_file_path)
			print("Blend file loaded successfully")
		except Exception as e:
			network_manager.preserve_network_on_file_load = False
			raise Exception(f"Failed to load blend file: {e}")
		
		# Clear the flag after file load
		network_manager.preserve_network_on_file_load = False
		
		# Restart network services if they were stopped during file load
		if not network_manager.communication_active:
			print("Restarting network services after file load")
			network_manager.start_communication_server()
		
		# Apply render settings
		self._apply_render_settings(render_settings)
		
		# Set up progress monitoring
		self._setup_render_monitoring(output_callback)
		
		# Start render
		self.render_status = "preparing"
		self.render_start_time = time.time()
		self.active_render = True
		
		if render_settings.get('animation', False):
			self._start_animation_render(render_settings)
		else:
			self._start_still_render(render_settings)
			
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
			
	def _setup_render_monitoring(self, output_callback):
		"""Set up render progress monitoring"""
		self.output_callback = output_callback
		
		# Clear previous handlers
		self._clear_render_handlers()
		
		# Add render handlers
		bpy.app.handlers.render_pre.append(_render_pre_handler)
		bpy.app.handlers.render_post.append(_render_post_handler)
		bpy.app.handlers.render_cancel.append(_render_cancel_handler)
		bpy.app.handlers.render_complete.append(_render_complete_handler)
		bpy.app.handlers.render_write.append(_render_write_handler)
	
	def _clear_render_handlers(self):
		"""Remove all render handlers"""
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
	
	def _sync_output_files(self, scene):
		"""Sync rendered output files back to source computer"""
		print(f"_sync_output_files called for frame {self.current_frame}")
		
		if not self.output_callback:
			print("No output callback available for syncing")
			return
			
		output_files = self._find_output_files(scene)
		print(f"Found {len(output_files)} output files to sync: {output_files}")
		
		for output_file in output_files:
			if os.path.exists(output_file):
				print(f"Syncing output file: {output_file}")
				try:
					self.output_callback(output_file, self.current_frame)
					print(f"Output callback executed for: {output_file}")
				except Exception as e:
					print(f"Error syncing output file {output_file}: {e}")
			else:
				print(f"Output file not found: {output_file}")
	
	def _find_output_files(self, scene):
		"""Find all output files for current frame"""
		output_files = []
		
		# Main render output
		if scene.render.filepath:
			# Handle frame number formatting
			base_path = scene.render.filepath
			if scene.render.use_file_extension:
				if scene.frame_current != scene.frame_start or self.frame_count > 1:
					# Add frame number
					path_parts = os.path.splitext(base_path)
					frame_str = f"{scene.frame_current:04d}"
					main_output = f"{path_parts[0]}{frame_str}{path_parts[1]}"
				else:
					main_output = base_path
			else:
				main_output = base_path
				
			output_files.append(bpy.path.abspath(main_output))
		
		# Compositor node outputs
		if scene.use_nodes and scene.node_tree:
			for node in scene.node_tree.nodes:
				if node.type == 'OUTPUT_FILE' and node.base_path:
					base_path = bpy.path.abspath(node.base_path)
					
					for input_socket in node.inputs:
						if input_socket.is_linked:
							# Build output filename for this socket
							if input_socket.name != 'Image':
								filename = f"{input_socket.name}{scene.frame_current:04d}"
							else:
								filename = f"Image{scene.frame_current:04d}"
								
							# Add file extension based on format
							if hasattr(node, 'format'):
								if node.format.file_format == 'PNG':
									filename += '.png'
								elif node.format.file_format == 'JPEG':
									filename += '.jpg'
								elif node.format.file_format == 'OPEN_EXR':
									filename += '.exr'
								else:
									filename += '.png'  # default
							else:
								filename += '.png'  # default
								
							output_path = os.path.join(base_path, filename)
							output_files.append(output_path)
		
		return output_files
		
	def _start_still_render(self, settings):
		"""Start still image render (on main thread)"""
		def render_on_main_thread():
			try:
				self.render_status = "rendering"
				bpy.ops.render.render('INVOKE_DEFAULT')
			except Exception as e:
				self.render_status = "error"
				self.render_error_message = str(e)
				self.active_render = False
				print(f"Render error: {e}")
			return None  # Don't repeat
		
		# Schedule render on main thread
		bpy.app.timers.register(render_on_main_thread, first_interval=0.1)
		
	def _start_animation_render(self, settings):
		"""Start animation render (on main thread)"""
		def render_on_main_thread():
			try:
				self.render_status = "rendering"
				bpy.ops.render.render('INVOKE_DEFAULT', animation=True)
			except Exception as e:
				self.render_status = "error"
				self.render_error_message = str(e)
				self.active_render = False
				print(f"Animation render error: {e}")
			return None  # Don't repeat
		
		# Schedule render on main thread
		bpy.app.timers.register(render_on_main_thread, first_interval=0.1)
		
	def cancel_render(self):
		"""Cancel active render"""
		if self.active_render:
			def cancel_on_main_thread():
				try:
					# Stop render if active
					if hasattr(bpy.ops.render, 'render') and bpy.context.scene:
						# Try to cancel any active render
						self.render_status = "cancelled"
						self.active_render = False
				except:
					self.render_status = "cancelled"
					self.active_render = False
				return None  # Don't repeat
			
			# Schedule cancel on main thread
			bpy.app.timers.register(cancel_on_main_thread, first_interval=0.1)
				
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
		self.queue_timer_active = False
		self.render_queue.clear()
		self.active_render = False
		self._clear_render_handlers()
		
		# Clear any pending timers safely
		try:
			# We can't unregister specific timer functions in Blender
			# Just set flags to stop timer loops
			pass
		except:
			pass

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
		
		print(f"Queuing output file for sync: {os.path.basename(file_path)} -> {source_ip}:{source_port}")
		self.sync_queue.append(sync_item)
		
		# Start sync thread if not running
		if not self.sync_active:
			print("Starting output sync thread")
			self.start_sync_thread()
		else:
			print(f"Output sync thread already active, queue size: {len(self.sync_queue)}")
			
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
				time.sleep(0.5)  # Wait for more files
				
	def _sync_output_file(self, sync_item):
		"""Sync individual output file back to source"""
		try:
			file_path = sync_item['file_path']
			
			if not os.path.exists(file_path):
				print(f"Output file not found: {file_path}")
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
					else:
						print(f"Failed to sync output file (attempt {attempt + 1}/{max_retries}): {os.path.basename(file_path)}")
						
				except Exception as e:
					print(f"Error syncing output file (attempt {attempt + 1}/{max_retries}): {e}")
				
				# Wait before retry (except on last attempt)
				if attempt < max_retries - 1:
					print(f"Retrying in {retry_delay} seconds...")
					time.sleep(retry_delay)
					retry_delay *= 2  # Exponential backoff
			
			print(f"Failed to sync output file after {max_retries} attempts: {os.path.basename(file_path)}")
				
		except Exception as e:
			print(f"Error syncing output file: {e}")

# Global instances
render_manager = RenderManager()
output_sync_manager = None  # Will be initialized when needed
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
		
		# Update ports from preferences
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
		
		# Run discovery in a separate thread to avoid blocking UI
		threading.Thread(target=self._scan_network, daemon=True).start()
		
		return {'FINISHED'}
	
	def _scan_network(self):
		discovered = network_manager.discover_nodes()
		
		def update_ui():
			# Update UI with discovered nodes
			context = bpy.context
			# Clear existing discovered nodes
			context.scene.discovered_nodes.clear()
			
			for node_id, node_info in discovered.items():
				item = context.scene.discovered_nodes.add()
				item.node_id = node_id
				item.name = node_info['name']
				item.ip = node_info['ip']
				item.port = node_info['port']
				item.blender_version = node_info['blender_version']
				item.requires_auth = node_info['requires_auth']
				
		# Schedule UI update on main thread
		bpy.app.timers.register(update_ui, first_interval=0.1)

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
			
			# Start communication server on source to receive output files
			if not network_manager.communication_active:
				network_manager.start_communication_server()
				print("Started communication server on source for output file reception")
			
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
			
			# Start communication server on source to receive output files
			if not network_manager.communication_active:
				network_manager.start_communication_server()
				print("Started communication server on source for output file reception")
			
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
		
		# Run scan in separate thread
		threading.Thread(target=self._scan_project, daemon=True).start()
		
		return {'FINISHED'}
	
	def _scan_project(self):
		def update_ui(dependencies, sync_changes):
			context = bpy.context
			props = context.scene.remote_render_props
			
			# Update external files warning
			props.external_files_count = len(dependencies['external'])
			props.show_external_warning = len(dependencies['external']) > 0
			
			# Clear existing sync files
			context.scene.sync_files.clear()
			
			if sync_changes:
				# Add files that need syncing
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
		
		try:
			# Scan dependencies
			dependencies = file_sync_manager.scan_blend_dependencies()
			
			# If connected to a node, check sync status
			sync_changes = None
			context = bpy.context
			props = context.scene.remote_render_props
			
			if props.selected_node:
				# Find connected node
				target_node = None
				for node in context.scene.discovered_nodes:
					if node.node_id == props.selected_node and node.is_connected:
						target_node = node
						break
				
				if target_node:
					# Get project root and create local manifest
					project_root = file_sync_manager.get_project_root()
					if project_root:
						local_manifest = file_sync_manager.get_directory_manifest(project_root)
						
						# Get remote manifest
						remote_manifest = network_manager.get_remote_manifest(
							target_node.ip,
							target_node.port,
							target_node.auth_token,
							props.project_name
						)
						
						if remote_manifest is not None:
							sync_changes = file_sync_manager.compare_manifests(local_manifest, remote_manifest)
			
			# Schedule UI update on main thread
			bpy.app.timers.register(lambda: update_ui(dependencies, sync_changes), first_interval=0.1)
		
		except Exception as e:
			print(f"Project scan failed: {e}")
			
			def update_error():
				context = bpy.context
				props = context.scene.remote_render_props
				props.sync_status = f"Scan failed: {e}"
			
			bpy.app.timers.register(update_error, first_interval=0.1)

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
		
		# Run sync in separate thread
		threading.Thread(
			target=self._sync_files,
			args=(target_node, selected_files, props.project_name),
			daemon=True
		).start()
		
		return {'FINISHED'}
	
	def _sync_files(self, target_node, selected_files, project_name):
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
						project_name,
						file_info.file_path,
						local_file_path
					)
					
					if success:
						success_count += 1
			
			def update_ui():
				context = bpy.context
				props = context.scene.remote_render_props
				props.sync_status = f"Synced {success_count}/{total_files} files"
				
				# Remove successfully synced files from list and re-scan
				if success_count > 0:
					bpy.ops.render_remote.scan_project()
			
			bpy.app.timers.register(update_ui, first_interval=0.1)
		
		except Exception as e:
			print(f"File sync failed: {e}")
			
			def update_error():
				context = bpy.context
				props = context.scene.remote_render_props
				props.sync_status = f"Sync failed: {e}"
			
			bpy.app.timers.register(update_error, first_interval=0.1)

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
		
		# Get blend file path on remote (assume synced)
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
		print(f"Sending render request to {target_node.ip}:{target_node.port}")
		print(f"Auth token: {'provided' if target_node.auth_token else 'missing'}")
		print(f"Project: {props.project_name}")
		print(f"Blend file: {remote_blend_path}")
		
		result = network_manager.send_render_request(
			target_node.ip,
			target_node.port,
			target_node.auth_token,
			props.project_name,
			remote_blend_path,
			render_settings
		)
		
		print(f"Render request result: {result}")
		
		if result and result.get('status') == 'success':
			self.report({'INFO'}, "Render started on remote computer")
			props.render_status = "Starting"
			props.monitor_render = True
			
			# Start progress monitoring
			self._start_progress_monitoring(context, target_node)
		else:
			error_msg = result.get('message', 'Unknown error') if result else 'Connection failed'
			print(f"Render failed with error: {error_msg}")
			self.report({'ERROR'}, f"Failed to start render: {error_msg}")
		
		return {'FINISHED'}
	
	def _start_progress_monitoring(self, context, target_node):
		"""Start monitoring render progress"""
		def monitor_progress():
			props = context.scene.remote_render_props
			
			if not props.monitor_render:
				return None  # Stop monitoring
			
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
				
				# Continue monitoring if render is active
				if props.render_status in ['preparing', 'rendering']:
					return 2.0  # Check every 2 seconds
				else:
					props.monitor_render = False
					return None  # Stop monitoring
			else:
				props.render_status = "Connection Error"
				props.monitor_render = False
				return None
		
		# Start monitoring timer
		bpy.app.timers.register(monitor_progress, first_interval=1.0)

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
	bl_label = "Remote Render Sync"
	bl_idname = "REMOTERENDER_PT_main_panel"
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"
	bl_category = "Launch"
	
	@classmethod
	def poll(cls, context):
		return context.preferences.addons[__package__].preferences.remote_enable
	
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
		"""Draw UI for Target mode (allow this computer to be used for rendering)"""
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
		"""Draw UI for Source mode (control remote rendering)"""
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
			
			# Still image render
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

@persistent
def cleanup_on_exit(dummy):
	"""Clean up network connections on Blender exit"""
	global network_manager, render_manager
	
	# Don't cleanup if we're preserving network during file load
	if network_manager and network_manager.preserve_network_on_file_load:
		print("Preserving network services during file load")
		return
		
	if network_manager:
		print("Cleaning up network manager on exit")
		network_manager.stop_discovery_server()
	if render_manager:
		render_manager.cleanup()

@persistent
def cleanup_on_load_pre(dummy):
	"""Clean up before loading files, but preserve network if rendering"""
	global network_manager, render_manager
	
	# Don't cleanup if we're preserving network during file load
	if network_manager and network_manager.preserve_network_on_file_load:
		print("Preserving network services during render file load")
		return
		
	# Only cleanup render manager, keep network for normal file loads
	if render_manager and not render_manager.active_render:
		render_manager.cleanup()

@persistent
def restore_network_on_load_post(dummy):
	"""Restore network services after file load if needed"""
	global network_manager
	
	# If we were preserving network and services stopped, restart them
	if (network_manager and 
		network_manager.preserve_network_on_file_load and 
		not network_manager.communication_active):
		print("Restoring network services after file load")
		# Small delay to ensure file is fully loaded
		def restore_services():
			if hasattr(bpy.context.scene, 'remote_render_props'):
				props = bpy.context.scene.remote_render_props
				if props.mode == 'TARGET':
					network_manager.start_communication_server()
			return None
		bpy.app.timers.register(restore_services, first_interval=1.0)



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
	global render_manager
	render_manager.render_status = "cancelled"
	render_manager.active_render = False
	print("Render cancelled")

@persistent
def _render_complete_handler(scene, depsgraph):
	"""Called when all rendering is complete"""
	global render_manager
	render_manager.render_status = "completed"
	render_manager.render_progress = 100.0
	render_manager.active_render = False
	render_manager._clear_render_handlers()
	print("All rendering completed")

@persistent
def _render_write_handler(scene, depsgraph):
	"""Called when frame is written to disk"""
	global render_manager
	# Find output files and sync them
	render_manager._sync_output_files(scene)
	
	# Update progress
	if render_manager.frame_count > 0:
		frames_completed = (render_manager.current_frame - scene.frame_start + 1)
		render_manager.render_progress = (frames_completed / render_manager.frame_count) * 100.0
	
	print(f"Frame {render_manager.current_frame} written to disk, progress: {render_manager.render_progress:.1f}%")
	print(f"Attempting to sync output files for frame {render_manager.current_frame}")



def register():
	for cls in classes:
		bpy.utils.register_class(cls)
		
	# Register property groups
	bpy.types.Scene.remote_render_props = bpy.props.PointerProperty(type=RemoteRenderProperties)
	bpy.types.Scene.discovered_nodes = bpy.props.CollectionProperty(type=RemoteNodeProperties)
	bpy.types.Scene.sync_files = bpy.props.CollectionProperty(type=SyncFileInfo)
	
	# Register cleanup handler
	bpy.app.handlers.load_pre.append(cleanup_on_exit)
	
	print("Remote Render Sync add-on registered with file synchronization")

def unregister():
	# Cleanup network manager
	global network_manager, render_manager
	if network_manager:
		network_manager.stop_discovery_server()
	
	# Cleanup render manager
	if render_manager:
		render_manager.cleanup()
	
	# Remove handlers
	if cleanup_on_exit in bpy.app.handlers.load_pre:
		bpy.app.handlers.load_pre.remove(cleanup_on_exit)
		
	# Unregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)
		
	# Remove properties
	del bpy.types.Scene.remote_render_props
	del bpy.types.Scene.discovered_nodes
	del bpy.types.Scene.sync_files
	
	print("Remote Render Sync add-on unregistered")