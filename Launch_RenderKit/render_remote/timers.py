import bpy

class TimerManager:
	"""Centralized timer management to prevent registration issues"""

	def __init__(self):
		self.active_timers = set()
		self.timer_callbacks = {}

	def register_timer(self, callback, interval=1.0, persistent=False):
		"""Register a timer with proper tracking"""
		if callback in self.active_timers:
			return None # Timer was cancelled

		def wrapper():
			try:
				# Check if callback was cancelled
				if callback not in self.active_timers:
					return None  # Timer was cancelled

				result = callback()

				# Handle different return values
				if result is None or result is False:
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
