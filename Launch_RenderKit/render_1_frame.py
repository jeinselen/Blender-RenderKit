# General features
import bpy
from bpy.app.handlers import persistent
import time
import json

# Local imports
from .render_variables import replaceVariables
from .utility_ffmpeg import process_ffmpeg
#from .utility_time import secondsToReadable
from . import utility_data

###########################################################################
# During render functions
# •Output location variables update
# •Remaining render time estimation

@persistent
def render_kit_frame_pre(scene):
	settings = scene.render_kit_settings
	
	# If video sequence is inactive and our current frame is not our starting frame, assume we're rendering a sequence
#	if not settings.sequence_active and settings.start_frame > -1 and settings.start_frame < scene.frame_current:
#		settings.sequence_active = True
	utility_data.render_set_sequence(scene.frame_current)
	
	# Save starting frame (this should only happen once during a sequence)
	utility_data.render_set_start_frame(scene.frame_current)
	
	# If file name processing is enabled and a sequence is underway, re-process output variables
	# Note: {serial} usage is not checked here as it should have already been completed by the render_kit_start function
	prefs = bpy.context.preferences.addons[__package__].preferences
	if prefs.render_variable_enable:
		# Filter render output file path
		if settings.output_file_path:
			# Replace scene filepath output with the processed version from the original saved version
			scene.render.filepath = replaceVariables(scene, settings.output_file_path)
		
		# Filter compositing node file paths
		compositing = scene.node_tree if bpy.app.version < tuple([5,0,0]) else scene.compositing_node_group
		if scene.render.use_compositing and compositing and settings.output_file_nodes:
			# Get the JSON data from the preferences string where it was stashed
			json_data = settings.output_file_nodes
			
			# If the JSON data is not empty, deserialize it and update the string values with new variables
			if json_data:
				node_settings = json.loads(json_data)
				
				# Get node data
				for node_name, node_data in node_settings.items():
					node = compositing.nodes.get(node_name)
					
					# Check if the node is a File Output node and unmuted
					if isinstance(node, bpy.types.CompositorNodeOutputFile) and not node.mute:
						if bpy.app.version < tuple([5,0,0]):
							# Reset base path
							node.base_path = node_data.get("directory", node.base_path)
							# Replace dynamic variables in the base path
							node.base_path = replaceVariables(scene, node.base_path)
						else:
							# Reset base path
							node.directory = node_data.get("directory", node.directory)
							# Replace dynamic variables in the base path
							node.directory = replaceVariables(scene, node.directory)
						
						# Get output port data
						output_port_data = node_data.get("outputs", {})
						for i, port_data in output_port_data.items():
							if bpy.app.version < tuple([5,0,0]):
								# --- Blender 4.x: still uses file_slots and ID properties ---
								output_port = node.file_slots[int(i)]
								if output_port:
									# Reset slot path
									output_port.path = port_data.get("path", output_port.path)
									# Replace dynamic variables in the slot path
									output_port.path = replaceVariables(scene, output_port.path)
							else:
								# --- Blender 5.x: use JSON data instead of IDProperties on file_output_items ---
								output_item = node.file_output_items[int(i)]
								if output_item:
									if isinstance(port_data, dict):
										original_name = port_data.get("name", output_item.name)
									else:
										# allow outputs to be stored as plain strings too
										# This should be revisited when 4.5 support is dropped
										original_name = port_data or output_item.name
									
									# Re-apply variables on the original name,
									# not on the already-expanded one
									output_item.name = replaceVariables(scene, original_name)



@persistent
def render_kit_frame_post(scene):
	settings = scene.render_kit_settings
	
	# If sequence rendering is currently active
#	if settings.sequence_active:
	if utility_data.render_get_sequence():
		# If it's not the last frame, estimate time remaining
		if scene.frame_current < scene.frame_end:
			# Elapsed time (Current - Render Start)
			render_time = time.time() - utility_data.render_get_start_time()
			# Divide by number of frames completed
			render_time /= scene.frame_current - utility_data.render_get_start_frame() + 1
			# Multiply by number of frames assumed unrendered (does not account for previously completed frames beyond the current frame)
			render_time *= scene.frame_end - scene.frame_current
			# Store estimated render time remaining
#			settings.estimated_time = secondsToReadable(render_time)
			utility_data.render_set_estimate(render_time)
			# print('Estimated Time Remaining: ' + settings.estimated_time)
		
		# If FFmpeg processing is enabled and command path exists
		prefs = bpy.context.preferences.addons[__package__].preferences
		if prefs.ffmpeg_processing and prefs.ffmpeg_exists:
			# If any of the FFmpeg options are enabled
			if settings.autosave_video_prores or settings.autosave_video_mp4 or settings.autosave_video_custom:
				# If path is different than previous, start a new FFmpeg process to compile the previous range of images
				# Or if this is the last frame in the render range
				if (settings.autosave_video_render_path and settings.autosave_video_render_path != scene.render.filepath) or (scene.frame_current == scene.frame_end):
					# Process FFmpeg outputs
					process_ffmpeg(scene, render_path=settings.autosave_video_render_path)
					
					# Track usage of output serial in FFmpeg outputs
					if settings.autosave_video_prores and '{serial}' in settings.autosave_video_prores_location:
#						settings.serial_used = True
						utility_data.render_set_serial(True)
					if settings.autosave_video_mp4 and '{serial}' in settings.autosave_video_mp4_location:
#						settings.serial_used = True
						utility_data.render_set_serial(True)
					if settings.autosave_video_custom and '{serial}' in settings.autosave_video_custom_location:
#						settings.serial_used = True
						utility_data.render_set_serial(True)
	
	# Store processed render path for checking against during a video sequence
	settings.autosave_video_render_path = scene.render.filepath
	settings.autosave_video_prores_path = replaceVariables(scene, settings.autosave_video_prores_location)
	settings.autosave_video_mp4_path = replaceVariables(scene, settings.autosave_video_mp4_location)
	settings.autosave_video_custom_path = replaceVariables(scene, settings.autosave_video_custom_location)