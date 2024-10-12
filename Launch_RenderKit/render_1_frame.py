# General features
import bpy
from bpy.app.handlers import persistent
import time

# Local imports
from .render_variables import replaceVariables
from .utility_time import secondsToReadable

###########################################################################
# During render function
# •Remaining render time estimation

@persistent
def render_kit_frame(scene):
	prefs = bpy.context.preferences.addons[__package__].preferences
	settings = bpy.context.scene.render_kit_settings
	
	# Save starting frame (before setting active to true, this should only happen once during a sequence)
	if not settings.estimated_render_time_active:
		settings.estimated_render_time_frame = bpy.context.scene.frame_current
	
	# If video sequence is inactive and our current frame is not our starting frame, assume we're rendering a sequence
	if not settings.autosave_video_sequence and settings.estimated_render_time_frame < bpy.context.scene.frame_current:
		settings.autosave_video_sequence = True
	
	# If file name processing is enabled and a sequence is underway, re-process output variables
	# Note: {serial} usage is not checked here as it should have already been completed by the render_kit_start function
	if settings.autosave_video_sequence and prefs.render_output_variables:
		next_frame = bpy.context.scene.frame_current + 1
		
		# Filter render output file path
		if len(settings.output_file_path) > 2:
			# Replace scene filepath output with the processed version from the original saved version
			scene.render.filepath = replaceVariables(settings.output_file_path, serial=settings.output_file_serial, scene_frame=next_frame)
		
		# Filter compositing node file paths
		elif bpy.context.scene.use_nodes and len(settings.output_file_nodes) > 2:
			# Get the JSON data from the preferences string where it was stashed
			json_data = settings.output_file_nodes
			
			# If the JSON data is not empty, deserialize it and update the string values with new variables
			if json_data:
				node_settings = json.loads(json_data)
				for node_name, node_data in node_settings.items():
					node = bpy.context.scene.node_tree.nodes.get(node_name)
					if isinstance(node, bpy.types.CompositorNodeOutputFile):
						# Replace dynamic variables
						node.base_path = replaceVariables(node.base_path, serial=settings.output_file_serial, scene_frame=next_frame)
						
						# Save and then process the sub-path property of each file slot
						for i, slot in enumerate(node.file_slots):
							# Replace dynamic variables
							slot.path = replaceVariables(slot.path, serial=settings.output_file_serial, scene_frame=next_frame)
	
	
	
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
