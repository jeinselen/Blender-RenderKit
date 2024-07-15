# General features
import bpy
from bpy.app.handlers import persistent
import time

# Local imports
from .utility_time import secondsToReadable

###########################################################################
# During render function
# •Remaining render time estimation

@persistent
def render_kit_frame(scene):
	settings = bpy.context.scene.render_kit_settings
	
	# Save starting frame (before setting active to true, this should only happen once during a sequence)
	if not settings.estimated_render_time_active:
		settings.estimated_render_time_frame = bpy.context.scene.frame_current
	
	# If video sequence is inactive and our current frame is not our starting frame, assume we're rendering a sequence
	if not settings.autosave_video_sequence and settings.estimated_render_time_frame < bpy.context.scene.frame_current:
		settings.autosave_video_sequence = True
	
	# If it's not the last frame, estimate time remaining
	if bpy.context.scene.frame_current < bpy.context.scene.frame_end:
		settings.estimated_render_time_active = True
		# Elapsed time (Current - Render Start)
		render_time = time.time() - float(settings.start_date)
		# Divide by number of frames completed
		render_time /= bpy.context.scene.frame_current - settings.estimated_render_time_frame + 1.0
		# Multiply by number of frames assumed unrendered (does not account for previously completed frames beyond the current frame)
		render_time *= bpy.context.scene.frame_end - bpy.context.scene.frame_current
		# Convert to readable and store
		settings.estimated_render_time_value = secondsToReadable(render_time)
		# print('Estimated Time Remaining: ' + settings.estimated_render_time_value)
	else:
		settings.estimated_render_time_active = False
