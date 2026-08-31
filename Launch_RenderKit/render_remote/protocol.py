import json
import os
import re
import struct
from .constants import PROTOCOL_MAX_MESSAGE_SIZE, PROTOCOL_MAX_FILE_SIZE, FILE_TRANSFER_CHUNK_SIZE

class ProtocolError(Exception):
	"""Raised when a network message or file payload violates protocol limits"""
	pass

def error_response(code, message):
	"""Build a structured error without local filesystem details"""
	return {'status': 'error', 'code': code, 'message': message}

def describe_error(error):
	"""Return a short, path-free description of an exception for remote responses"""
	if isinstance(error, OSError) and error.strerror:
		return error.strerror
	text = str(error).strip()
	if not text:
		return type(error).__name__
	# Never leak local filesystem layout back to the peer
	text = re.sub(r'(?:[A-Za-z]:)?[\\/](?:[^\\/\s]+[\\/])*[^\\/\s]*', '[path]', text)
	return text.strip() or type(error).__name__

def validate_message(msg, schema):
	"""Check that msg contains all required fields with the correct types.

	schema: {field_name: type_or_tuple_of_types}
	Returns a list of human-readable error strings; empty means valid.
	"""
	errors = []
	for field, expected in schema.items():
		if field not in msg:
			errors.append(f"missing required field '{field}'")
		elif not isinstance(msg[field], expected):
			type_desc = expected.__name__ if isinstance(expected, type) else repr(expected)
			errors.append(f"field '{field}' must be {type_desc}")
	return errors

def validate_file_size(file_size):
	"""Normalize and validate file payload sizes"""
	try:
		file_size = int(file_size)
	except (TypeError, ValueError):
		raise ProtocolError("Invalid file size")
	if file_size < 0:
		raise ProtocolError("Invalid file size")
	if file_size > PROTOCOL_MAX_FILE_SIZE:
		raise ProtocolError("File is too large")
	return file_size

def recv_exact(sock, byte_count):
	"""Read exactly byte_count bytes from a socket"""
	if byte_count < 0:
		raise ProtocolError("Invalid byte count")
	chunks = []
	remaining = byte_count
	while remaining > 0:
		chunk = sock.recv(min(remaining, 64 * 1024))
		if not chunk:
			raise ProtocolError("Connection closed")
		chunks.append(chunk)
		remaining -= len(chunk)
	return b''.join(chunks)

def discard_exact(sock, byte_count):
	"""Read and discard exactly byte_count bytes so the message stream stays framed"""
	remaining = max(0, int(byte_count))
	while remaining > 0:
		chunk = sock.recv(min(remaining, FILE_TRANSFER_CHUNK_SIZE))
		if not chunk:
			raise ProtocolError("Connection closed")
		remaining -= len(chunk)

def send_message(sock, message):
	"""Send a bounded length-prefixed JSON message"""
	try:
		message_data = json.dumps(message).encode('utf-8')
	except (TypeError, ValueError):
		raise ProtocolError("Message is not JSON serializable")
	if len(message_data) > PROTOCOL_MAX_MESSAGE_SIZE:
		raise ProtocolError("Message is too large")
	sock.sendall(struct.pack('!I', len(message_data)))
	sock.sendall(message_data)

def recv_message(sock):
	"""Receive a bounded length-prefixed JSON message"""
	length_data = recv_exact(sock, 4)
	message_length = struct.unpack('!I', length_data)[0]
	if message_length <= 0:
		raise ProtocolError("Invalid message size")
	if message_length > PROTOCOL_MAX_MESSAGE_SIZE:
		raise ProtocolError("Message is too large")
	message_data = recv_exact(sock, message_length)
	try:
		return json.loads(message_data.decode('utf-8'))
	except (UnicodeDecodeError, json.JSONDecodeError):
		raise ProtocolError("Invalid JSON message")

def send_file(sock, file_path, file_size=None, should_cancel=None):
	"""Send a bounded file payload"""
	if file_size is None:
		file_size = os.path.getsize(file_path)
	file_size = validate_file_size(file_size)
	bytes_sent = 0
	with open(file_path, 'rb') as f:
		while bytes_sent < file_size:
			if should_cancel and should_cancel():
				raise ProtocolError("File transfer cancelled")
			chunk = f.read(min(FILE_TRANSFER_CHUNK_SIZE, file_size - bytes_sent))
			if not chunk:
				raise ProtocolError("Incomplete file read")
			sock.sendall(chunk)
			bytes_sent += len(chunk)

def recv_file(sock, target_file_path, file_size, should_cancel=None):
	"""Receive a bounded file payload into a temporary sibling path.

	The announced payload is always consumed, even when the local write fails, so the
	caller can report the real error over a still-framed connection.
	"""
	file_size = validate_file_size(file_size)
	temp_file_path = f"{target_file_path}.part"
	bytes_received = 0
	cancelled = False
	try:
		with open(temp_file_path, 'wb') as f:
			while bytes_received < file_size:
				if should_cancel and should_cancel():
					cancelled = True
					raise ProtocolError("File transfer cancelled")
				chunk = recv_exact(sock, min(FILE_TRANSFER_CHUNK_SIZE, file_size - bytes_received))
				bytes_received += len(chunk)
				f.write(chunk)
		os.replace(temp_file_path, target_file_path)
	except Exception:
		try:
			if os.path.exists(temp_file_path):
				os.remove(temp_file_path)
		except OSError:
			pass
		if not cancelled:
			try:
				discard_exact(sock, file_size - bytes_received)
			except Exception:
				pass
		raise
