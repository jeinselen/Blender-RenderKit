import hashlib
import ipaddress
import re
from pathlib import Path
try:
	import bpy
except ImportError:
	bpy = None

ADDON_VERSION = "0.2.0"  # mirrors blender_manifest.toml version field

def addon_package_from_module_package(package_name):
	"""Return the root add-on package for this render_remote subpackage."""
	return str(package_name).rsplit('.render_remote', 1)[0]

ADDON_PACKAGE = addon_package_from_module_package(__package__)

PROTOCOL_MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB JSON envelope limit
PROTOCOL_MAX_FILE_SIZE = 128 * 1024 * 1024 * 1024  # 128GB file transfer limit
AUTH_PBKDF2_ITERATIONS = 200_000
AUTH_CHALLENGE_TIMEOUT = 60
AUTH_TOKEN_TIMEOUT = 60 * 60
AUTH_MAX_CHALLENGES = 256
AUTH_RATE_LIMIT_WINDOW = 60   # seconds in which failures are counted
AUTH_RATE_LIMIT_MAX = 5       # max failures per window before IP is temporarily blocked
DISCOVERY_REPLY_TIMEOUT = 1.0
CLIENT_READ_TIMEOUT = 30.0
DISCOVERY_BROADCAST_TIMEOUT = 0.5
PROJECT_ID_HASH_LENGTH = 12
PROJECT_ID_SLUG_LENGTH = 60
INPUT_MANIFEST_FILENAME = ".render_remote_input_manifest.json"
INPUT_MANIFEST_VERSION = 1
OUTPUT_SYNC_POLL_INTERVAL = 2.0
OUTPUT_SYNC_QUIET_PERIOD = 6.0
OUTPUT_SYNC_POST_PROCESS_TIMEOUT = 60.0

FILE_TRANSFER_CHUNK_SIZE = 64 * 1024

def default_remote_cache_directory():
	"""Return an absolute cache directory that does not depend on a saved blend file."""
	return str(Path.home().joinpath("Documents", "Launch_RenderKit_RemoteCache"))

LAN_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
	'10.0.0.0/8',
	'172.16.0.0/12',
	'192.168.0.0/16',
	'169.254.0.0/16',
	'127.0.0.0/8',
	'::1/128',
	'fc00::/7',
	'fe80::/10',
))

def parse_ip_address(ip):
	"""Parse an IP address string, including scoped IPv6 addresses"""
	ip_string = str(ip).strip()
	if '%' in ip_string:
		ip_string = ip_string.split('%', 1)[0]
	address = ipaddress.ip_address(ip_string)
	if getattr(address, 'ipv4_mapped', None):
		return address.ipv4_mapped
	return address

def is_allowed_lan_ip(ip):
	"""Return True only for loopback, private, and link-local peers"""
	try:
		address = parse_ip_address(ip)
	except ValueError:
		return False
	return any(address in network for network in LAN_ALLOWED_NETWORKS)

def normalize_project_id(project_name):
	"""Convert a user-facing project name into a bounded cache directory id"""
	raw_name = str(project_name or "default").strip() or "default"
	digest = hashlib.sha256(raw_name.encode('utf-8')).hexdigest()[:PROJECT_ID_HASH_LENGTH]
	slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', raw_name).strip('._-')
	slug = slug[:PROJECT_ID_SLUG_LENGTH].strip('._-') or "project"
	return f"{slug}-{digest}"

def build_source_project_cache_name(blend_file_path=None):
	"""Build a stable source-side cache identity for one blend project"""
	if not blend_file_path:
		try:
			blend_file_path = bpy.data.filepath
		except Exception:
			blend_file_path = None

	if not blend_file_path:
		raise ValueError("Saved blend file required for remote project cache identity")

	blend_path = str(Path(blend_file_path).expanduser().resolve(strict=False))
	blend_name = Path(blend_path).name or "project.blend"
	project_name = Path(blend_name).stem or "project"
	blend_hash = hashlib.sha256(blend_path.encode('utf-8')).hexdigest()[:PROJECT_ID_HASH_LENGTH]
	return f"{project_name}-{blend_name}-{blend_hash}"
