# General features
import bpy
from bpy.app.handlers import persistent
import time
import json

# Local imports
from .render_variables import replaceVariables
from .utility_ffmpeg import processFFmpeg
from .utility_time import secondsToReadable

###########################################################################
# During render functions
# •Output location variables update
# •Remaining render time estimation

@persistent
def render_kit_frame_pre(scene):
	prefs = bpy.context.preferences.addons[__package__].preferences
	settings = scene.render_kit_settings
	
	# Save starting frame (before setting active to true, this should only happen once during a sequence)
	if not settings.estimated_render_time_active:
		settings.estimated_render_time_frame = scene.frame_current
	
	# If video sequence is inactive and our current frame is not our starting frame, assume we're rendering a sequence
	if not settings.sequence_rendering_status and settings.estimated_render_time_frame < scene.frame_current:
		settings.sequence_rendering_status = True
	
	# If file name processing is enabled and a sequence is underway, re-process output variables
	# Note: {serial} usage is not checked here as it should have already been completed by the render_kit_start function
	if prefs.render_variable_enable:
		
		# Filter render output file path
		if settings.output_file_path:
			# Replace scene filepath output with the processed version from the original saved version
			scene.render.filepath = replaceVariables(settings.output_file_path)
			
		# Filter compositing node file paths
		if scene.use_nodes and settings.output_file_nodes:
			# Get the JSON data from the preferences string where it was stashed
			json_data = settings.output_file_nodes
			
			# If the JSON data is not empty, deserialize it and update the string values with new variables
			if json_data:
				node_settings = json.loads(json_data)
				
				# Get node data
				for node_name, node_data in node_settings.items():
					node = scene.node_tree.nodes.get(node_name)
					if isinstance(node, bpy.types.CompositorNodeOutputFile):
						# Reset base path
						node.base_path = node_data.get("base_path", node.base_path)
						# Replace dynamic variables in the base path
						node.base_path = replaceVariables(node.base_path)
						
						# Get slot data
						file_slots_data = node_data.get("file_slots", {})
						for i, slot_data in file_slots_data.items():
							slot = node.file_slots[int(i)]
							if slot:
								# Reset slot path
								slot.path = slot_data.get("path", slot.path)
								# Replace dynamic variables in the slot path
								slot.path = replaceVariables(slot.path)



@persistent
def render_kit_frame_post(scene):
	prefs = bpy.context.preferences.addons[__package__].preferences
	settings = scene.render_kit_settings
	
	# If it's not the last frame, estimate time remaining
	if scene.frame_current < scene.frame_end:
		settings.estimated_render_time_active = True
		# Elapsed time (Current - Render Start)
		render_time = time.time() - float(settings.start_date)
		# Divide by number of frames completed
		render_time /= scene.frame_current - settings.estimated_render_time_frame + 1.0
		# Multiply by number of frames assumed unrendered (does not account for previously completed frames beyond the current frame)
		render_time *= scene.frame_end - scene.frame_current
		# Convert to readable and store
		settings.estimated_render_time_value = secondsToReadable(render_time)
		# print('Estimated Time Remaining: ' + settings.estimated_render_time_value)
	else:
		settings.estimated_render_time_active = False
	
	
	
	# If sequence rendering is ongoing, FFmpeg processing is enabled, and command path exists
	if settings.sequence_rendering_status and prefs.ffmpeg_processing and prefs.ffmpeg_exists:
		# If path is different than previous, start a new FFmpeg process to compile the previous range of images
		# Or if this is the last frame in the render range
		if (settings.autosave_video_render_path and settings.autosave_video_render_path != scene.render.filepath) or scene.frame_current == scene.frame_end:
			processFFmpeg(render_path=settings.autosave_video_render_path)
	
	# Store processed render path for checking against during a video sequence
	settings.autosave_video_render_path = scene.render.filepath
	settings.autosave_video_prores_path = replaceVariables(settings.autosave_video_prores_location)
	settings.autosave_video_mp4_path = replaceVariables(settings.autosave_video_mp4_location)
	settings.autosave_video_custom_path = replaceVariables(settings.autosave_video_custom_location)
