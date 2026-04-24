import bpy
import hashlib
import hmac
import json
import os
import re
import socket
import ssl
import threading
import time
from datetime import datetime
from pathlib import Path
from .constants import (ADDON_PACKAGE, ADDON_VERSION, DISCOVERY_REPLY_TIMEOUT, CLIENT_READ_TIMEOUT,
                        DISCOVERY_BROADCAST_TIMEOUT, LAN_ALLOWED_NETWORKS,
                        INPUT_MANIFEST_FILENAME, INPUT_MANIFEST_VERSION,
                        normalize_project_id, parse_ip_address, is_allowed_lan_ip,
                        default_remote_cache_directory)
from .paths import (PathSecurityError, normalize_relative_path, resolve_under_root,
                    relative_path_under_root, is_reserved_input_manifest_path)
from .protocol import (ProtocolError, error_response, validate_message, validate_file_size,
                       send_message, recv_message, send_file, recv_file)
from .auth import SecureConnection
from .file_sync import file_sync_manager
from .output_monitor import OutputFileMonitor
from .timers import timer_manager

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
		self._discovery_ready_event = threading.Event()
		self._communication_ready_event = threading.Event()
		self._discovery_start_error = None
		self._communication_start_error = None
		self.last_error = ""

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
			prefs = bpy.context.preferences.addons[ADDON_PACKAGE].preferences
			self.discovery_port = prefs.remote_discovery_port
			self.communication_port = prefs.remote_communication_port
			self._cached_cache_root = self._resolve_cache_root(prefs.remote_cache_directory)
		except (AttributeError, KeyError):
			pass

	def _resolve_cache_root(self, configured_path):
		"""Resolve the configured cache root, falling back when blend-relative paths are unusable."""
		path_text = str(configured_path or '').strip()
		if not path_text:
			return default_remote_cache_directory()
		if path_text.startswith("//") and not getattr(bpy.data, 'filepath', ''):
			return default_remote_cache_directory()

		resolved_path = bpy.path.abspath(path_text)
		if not resolved_path or str(resolved_path).startswith("//"):
			return default_remote_cache_directory()
		return str(Path(resolved_path).expanduser().resolve())

	def _is_allowed_peer(self, ip):
		"""Allow only LAN-local peers for Render Remote sockets"""
		return is_allowed_lan_ip(ip)

	def _get_cache_root(self):
		"""Return the cached remote cache root; must be populated by update_ports_from_preferences on the main thread before handler threads call this."""
		if self._cached_cache_root is not None:
			return self._cached_cache_root
		try:
			prefs = bpy.context.preferences.addons[ADDON_PACKAGE].preferences
			return self._resolve_cache_root(prefs.remote_cache_directory)
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
		except ProtocolError:
			try:
				sock.close()
			except OSError:
				pass
			raise
		except (ssl.SSLError, ConnectionResetError, OSError) as e:
			try:
				sock.close()
			except OSError:
				pass
			raise ProtocolError(
				f"TLS connection to {ip}:{port} failed: {e}. "
				"Confirm the target Render Remote service is running and updated."
			)
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
			if self.communication_active:
				return True
			self.discovery_active = False

		if not self.configure_authentication(passcode):
			print("Remote render target service requires an authentication passcode")
			return False

		self._shutdown_requested = False

		# Start TCP/TLS first so discovery never advertises a target that cannot accept jobs.
		if not self.start_communication_server():
			self.clear_authentication()
			return False

		self._discovery_ready_event.clear()
		self._discovery_start_error = None
		self.discovery_active = True
		self.discovery_thread = threading.Thread(
			target=self._discovery_server_loop,
			args=(node_name, bool(passcode)),
			daemon=True
		)
		self.discovery_thread.start()

		if not self._discovery_ready_event.wait(timeout=2):
			print("Render Remote: discovery server did not start in time")
			self.discovery_active = False
			self.stop_communication_server(force=True)
			self.clear_authentication()
			return False

		if self._discovery_start_error:
			print(f"Render Remote: discovery server failed to start: {self._discovery_start_error}")
			self.discovery_active = False
			self.stop_communication_server(force=True)
			self.clear_authentication()
			return False

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
			return True

		self.update_ports_from_preferences()

		# Set up TLS certificate on the main thread before spawning the server daemon
		try:
			cert_dir = bpy.utils.user_resource('CONFIG', path='render_remote')
			os.makedirs(cert_dir, exist_ok=True)
			self.security.prepare_tls(cert_dir)
		except Exception as e:
			print(f"Render Remote: TLS setup failed — cannot start communication server: {e}")
			self.communication_active = False
			return False

		print(f"Starting communication server on port {self.communication_port}...")
		self._communication_ready_event.clear()
		self._communication_start_error = None
		self.communication_active = True
		self.communication_thread = threading.Thread(
			target=self._communication_server_loop,
			daemon=True
		)
		self.communication_thread.start()

		if not self._communication_ready_event.wait(timeout=2):
			print("Render Remote: communication server did not start in time")
			self.communication_active = False
			return False

		if self._communication_start_error:
			print(f"Render Remote: communication server failed to start: {self._communication_start_error}")
			self.communication_active = False
			return False

		print(f"Communication server thread started")
		return True

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
			self._discovery_ready_event.set()

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
			self._discovery_start_error = str(e)
			self.discovery_active = False
			self._discovery_ready_event.set()
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
				self._communication_start_error = f"Could not bind port {self.communication_port}"
				self.communication_active = False
				self._communication_ready_event.set()
				print(f"Failed to bind communication server to any address on port {self.communication_port}")
				return

			server_sock.listen(10)  # Increased queue size for multiple file transfers
			server_sock.settimeout(DISCOVERY_REPLY_TIMEOUT)
			self._communication_ready_event.set()

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
			self._communication_start_error = str(e)
			self.communication_active = False
			self._communication_ready_event.set()
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
		from .constants import AUTH_TOKEN_TIMEOUT
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
		from .render import render_manager
		try:
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
		from .render import render_manager
		try:
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
		from .render import render_manager
		try:
			relative_path = message.get('relative_path')
			if not relative_path:
				return {'status': 'error', 'message': 'File path required'}

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
		from .render import render_manager
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
		from .render import render_manager
		try:
			status = render_manager.get_render_status()
			return {'status': 'success', 'render_status': status}
		except Exception as e:
			print(f"Status request failed: {e}")
			return error_response('status_request_failed', 'Status request failed')

	def _handle_render_cancel(self, message):
		"""Handle render cancellation request"""
		from .render import render_manager
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

		def add_class_c_broadcast(ip_str):
			try:
				ip_obj = parse_ip_address(ip_str)
			except ValueError:
				return
			if ip_obj.version != 4 or not self._is_allowed_peer(str(ip_obj)) or ip_obj.is_loopback:
				return
			parts = str(ip_obj).split('.')
			if len(parts) == 4:
				addrs.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")

		add_class_c_broadcast(self._get_local_ip())

		try:
			for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
				ip_str = info[4][0]
				add_class_c_broadcast(ip_str)
		except Exception:
			pass

		try:
			import subprocess
			result = subprocess.run(
				['ifconfig'],
				check=False,
				capture_output=True,
				text=True,
				timeout=2
			)
			for match in re.finditer(r'\binet\s+(\d+\.\d+\.\d+\.\d+).*?\bbroadcast\s+(\d+\.\d+\.\d+\.\d+)', result.stdout):
				ip_str, broadcast_str = match.groups()
				if self._is_allowed_peer(ip_str):
					addrs.add(broadcast_str)
					add_class_c_broadcast(ip_str)
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
		from .constants import AUTH_PBKDF2_ITERATIONS
		self.last_error = ""
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
			self.last_error = response.get('message', 'Authentication failed')

		except Exception as e:
			self.last_error = str(e)
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

	def request_file_from_target(self, ip, port, auth_token, relative_path, manifest_entry=None, source_project_root=None):
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

					if not source_project_root:
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

# Global network manager singleton
network_manager = NetworkManager()
