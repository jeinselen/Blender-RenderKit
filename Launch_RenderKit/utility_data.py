import threading

_lock = threading.Lock()
_state = {
	"start_time": 0.0,
	"start_frame": -1,
	"serial_used": False,
	"sequence_active": False,
	"estimated_time": -1.0,
}

##################################################
# Set data groups

def render_set_start(start_time: float):
	with _lock:
		_state["start_time"] = float(start_time)
		_state["start_frame"] = -1
		_state["serial_used"] = False
		_state["sequence_active"] = False
		_state["estimated_time"] = -1.0

def render_set_end():
	with _lock:
		_state["start_time"] = 0.0
		_state["start_frame"] = -1
		_state["sequence_active"] = False
		_state["estimated_time"] = -1.0

# Set individual data elements

def render_set_start_time(time: float):
	with _lock:
		_state["start_time"] = float(time)

def render_set_start_frame(frame: int):
	with _lock:
		if _state["start_frame"] < 0:
			_state["start_frame"] = frame

def render_set_serial(used: bool):
	with _lock:
		_state["serial_used"] = used

def render_set_sequence(frame: int):
	with _lock:
		if not _state["sequence_active"] and _state["start_frame"] > -1 and _state["start_frame"] < frame:
			_state["sequence_active"] = True

def render_set_estimate(time: float):
	with _lock:
		_state["estimated_time"] = float(time)

##################################################
# Get data groups

def render_get_data():
	# UI uses this to read a consistent view
	with _lock:
		return dict(_state)

# Get individual data elements

def render_get_start_time():
	with _lock:
		return _state["start_time"]
	
def render_get_start_frame():
	with _lock:
		return _state["start_frame"]

def render_get_serial():
	with _lock:
		return _state["serial_used"]

def render_get_sequence():
	with _lock:
		return _state["sequence_active"]
	
def render_get_estimate():
	with _lock:
		return _state["estimated_time"]