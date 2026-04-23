import hashlib
import hmac
import json
import os
import secrets
import ssl
import subprocess
import threading
import time
from .constants import (AUTH_PBKDF2_ITERATIONS, AUTH_CHALLENGE_TIMEOUT, AUTH_TOKEN_TIMEOUT,
                        AUTH_MAX_CHALLENGES, AUTH_RATE_LIMIT_WINDOW, AUTH_RATE_LIMIT_MAX)
from .protocol import ProtocolError

class SecureConnection:
	"""Handles authentication state for remote TCP messages"""

	def __init__(self):
		self._lock = threading.RLock()
		self.auth_tokens = {}
		self.auth_challenges = {}
		self.connection_timeout = 300  # 5 minutes
		self._auth_failures = {}       # ip -> [timestamp, ...]
		self._cert_path = None
		self._key_path = None
		self._tls_ready = False

	# ---- TLS certificate management ----

	def prepare_tls(self, cert_dir):
		"""Generate or load the node TLS cert. Must be called from the main thread before starting servers."""
		cert_path = os.path.join(cert_dir, 'server.crt')
		key_path = os.path.join(cert_dir, 'server.key')
		if not (os.path.exists(cert_path) and os.path.exists(key_path)):
			try:
				subprocess.run(
					['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
					 '-keyout', key_path, '-out', cert_path,
					 '-days', '3650', '-nodes', '-subj', '/CN=render-remote'],
					check=True, capture_output=True
				)
				if os.name != 'nt':
					os.chmod(key_path, 0o600)
					os.chmod(cert_path, 0o600)
			except (subprocess.CalledProcessError, FileNotFoundError) as e:
				raise RuntimeError(
					f"Cannot generate TLS certificate — ensure openssl is installed and on PATH: {e}"
				)
		with self._lock:
			self._cert_path = cert_path
			self._key_path = key_path
			self._tls_ready = True

	def get_cert_fingerprint(self):
		"""Return the SHA-256 hex fingerprint of this node's TLS certificate."""
		with self._lock:
			cert_path = self._cert_path
		if not cert_path or not os.path.exists(cert_path):
			return None
		try:
			import base64
			with open(cert_path, 'rb') as f:
				pem_data = f.read().decode('ascii', errors='replace')
			lines = pem_data.strip().splitlines()
			der_b64 = ''.join(l for l in lines if not l.startswith('-----'))
			der_bytes = base64.b64decode(der_b64)
			return hashlib.sha256(der_bytes).hexdigest()
		except Exception:
			return None

	def regenerate_cert(self):
		"""Delete and regenerate the TLS certificate. Call from main thread only."""
		with self._lock:
			cert_path, key_path = self._cert_path, self._key_path
			self._tls_ready = False
		for path in (cert_path, key_path):
			if path and os.path.exists(path):
				try:
					os.remove(path)
				except OSError:
					pass
		if cert_path:
			self.prepare_tls(os.path.dirname(cert_path))

	def server_ssl_context(self):
		"""Return a TLS server context loaded with this node's certificate."""
		with self._lock:
			ready, cert_path, key_path = self._tls_ready, self._cert_path, self._key_path
		if not ready:
			raise RuntimeError("TLS is not ready — call prepare_tls() from the main thread first")
		ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
		ctx.load_cert_chain(cert_path, key_path)
		return ctx

	def client_ssl_context(self):
		"""Return a TLS client context that skips CA validation (fingerprint verified separately)."""
		ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
		ctx.check_hostname = False
		ctx.verify_mode = ssl.CERT_NONE  # fingerprint-pinning done post-handshake; see verify_peer_fingerprint
		return ctx

	def _fingerprint_store_path(self):
		with self._lock:
			cert_path = self._cert_path
		if cert_path:
			return os.path.join(os.path.dirname(cert_path), 'known_nodes.json')
		return None

	def verify_peer_fingerprint(self, ssl_sock, node_id):
		"""TOFU fingerprint check: auto-pin first connection; reject mismatches."""
		peer_cert_der = ssl_sock.getpeercert(binary_form=True)
		if peer_cert_der is None:
			raise ProtocolError("Peer provided no TLS certificate")
		fingerprint = hashlib.sha256(peer_cert_der).hexdigest()

		store_path = self._fingerprint_store_path()
		if store_path is None:
			return fingerprint  # TLS not fully configured; skip pinning

		try:
			with open(store_path, 'r', encoding='utf-8') as f:
				store = json.load(f)
		except (FileNotFoundError, json.JSONDecodeError):
			store = {}

		pinned = store.get(node_id)
		if pinned is None:
			store[node_id] = fingerprint
			try:
				with open(store_path, 'w', encoding='utf-8') as f:
					json.dump(store, f, indent=2)
			except OSError as e:
				print(f"Render Remote: Could not save TLS fingerprint pin: {e}")
			print(f"Render Remote: TOFU pinned fingerprint for {node_id}: {fingerprint[:16]}...")
		elif not hmac.compare_digest(pinned, fingerprint):
			raise ProtocolError(
				f"TLS fingerprint mismatch for {node_id} — connection refused. "
				f"If the remote certificate changed legitimately, clear the pin in preferences."
			)
		return fingerprint

	# ---- Rate-limit helpers ----

	def _record_auth_failure(self, ip):
		now = time.time()
		with self._lock:
			bucket = self._auth_failures.setdefault(ip, [])
			bucket.append(now)
			self._auth_failures[ip] = [t for t in bucket if now - t < AUTH_RATE_LIMIT_WINDOW]

	def _is_auth_blocked(self, ip):
		now = time.time()
		with self._lock:
			recent = [t for t in self._auth_failures.get(ip, []) if now - t < AUTH_RATE_LIMIT_WINDOW]
			return len(recent) >= AUTH_RATE_LIMIT_MAX

	# ---- Token generation ----

	def generate_auth_token(self):
		"""Generate a secure authentication token"""
		return secrets.token_urlsafe(32)

	def hash_password(self, password, salt=None):
		"""Hash password with salt for secure storage"""
		if salt is None:
			salt = secrets.token_hex(16)
		return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), AUTH_PBKDF2_ITERATIONS), salt

	def build_auth_proof(self, password_hash, client_nonce, server_nonce):
		"""Build a challenge response proof without sending the passcode"""
		message = f"{client_nonce}:{server_nonce}".encode()
		return hmac.new(password_hash, message, hashlib.sha256).hexdigest()

	def create_challenge(self, ip, salt):
		"""Create a short-lived authentication challenge"""
		with self._lock:
			self.cleanup_expired_auth()
			if len(self.auth_challenges) >= AUTH_MAX_CHALLENGES:
				oldest_nonce = min(
					self.auth_challenges,
					key=lambda nonce: self.auth_challenges[nonce].get('created', 0)
				)
				self.auth_challenges.pop(oldest_nonce, None)

			client_nonce = secrets.token_urlsafe(24)
			server_nonce = secrets.token_urlsafe(24)
			self.auth_challenges[client_nonce] = {
				'server_nonce': server_nonce,
				'created': time.time(),
				'ip': ip
			}
			return {
				'client_nonce': client_nonce,
				'server_nonce': server_nonce,
				'salt': salt,
				'iterations': AUTH_PBKDF2_ITERATIONS,
				'algorithm': 'pbkdf2_sha256_hmac_sha256'
			}

	def consume_challenge(self, client_nonce, server_nonce, ip):
		"""Fetch and remove a valid challenge"""
		with self._lock:
			challenge = self.auth_challenges.pop(client_nonce, None)
			if not challenge:
				return None
			if challenge.get('server_nonce') != server_nonce:
				return None
			if challenge.get('ip') != ip:
				return None
			if time.time() - challenge.get('created', 0) > AUTH_CHALLENGE_TIMEOUT:
				return None
			return challenge

	def issue_auth_token(self, ip):
		"""Issue an expiring token bound to the peer IP"""
		with self._lock:
			auth_token = self.generate_auth_token()
			now = time.time()
			self.auth_tokens[auth_token] = {
				'created': now,
				'expires': now + AUTH_TOKEN_TIMEOUT,
				'ip': ip
			}
			return auth_token

	def verify_auth_token(self, auth_token, ip):
		"""Verify an auth token and remove stale tokens"""
		with self._lock:
			self.cleanup_expired_auth()
			token_info = self.auth_tokens.get(auth_token)
			if not token_info:
				return False
			if not hmac.compare_digest(token_info.get('ip', ''), ip):
				return False
			if token_info.get('expires', 0) < time.time():
				self.auth_tokens.pop(auth_token, None)
				return False
			return True

	def cleanup_expired_auth(self):
		"""Remove expired auth tokens and challenges"""
		with self._lock:
			now = time.time()
			self.auth_tokens = {
				token: info for token, info in self.auth_tokens.items()
				if info.get('expires', 0) >= now
			}
			self.auth_challenges = {
				nonce: info for nonce, info in self.auth_challenges.items()
				if now - info.get('created', 0) <= AUTH_CHALLENGE_TIMEOUT
			}

	def clear_authentication(self):
		"""Clear all token and challenge state"""
		with self._lock:
			self.auth_tokens.clear()
			self.auth_challenges.clear()
