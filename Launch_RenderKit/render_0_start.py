# General features
import bpy
from bpy.app.handlers import persistent
import time
import json

# Local imports
from .render_variables import replaceVariables

###########################################################################
# Pre-render function
# •Set render status variables
# •Save start time for calculations
# •Replace output variables

@persistent
def render_kit_start(scene):
	prefs = bpy.context.preferences.addons[__package__].preferences
	settings = scene.render_kit_settings
	
	# Save start time in seconds as a string to the addon settings
	settings.start_date = str(time.time())
	
	# Set estimated render time active to false (must render at least one frame before estimating time remaining)
	settings.estimated_render_time_active = False
	
	# Set video sequence tracking (separate from render active above)
	settings.sequence_rendering_status = False
	
	# Track usage of the output serial usage globally to ensure it can be accessed before/after frame rendering
	# Set it to false ahead of processing to ensure no errors occur (usually only if there's a crash of some sort)
	settings.output_file_serial_used = False
	
	# Reset FFmpeg paths
	settings.autosave_video_render_path = ""
	settings.autosave_video_prores_path = ""
	settings.autosave_video_mp4_path = ""
	settings.autosave_video_custom_path = ""
	
	# Track usage of output serial in FFmpeg outputs only if enabled
	if prefs.ffmpeg_processing and prefs.ffmpeg_exists:
		if settings.autosave_video_prores:
			settings.output_file_serial_used = True if '{serial}' in settings.autosave_video_prores_location else False
		if settings.autosave_video_mp4:
			settings.output_file_serial_used = True if '{serial}' in settings.autosave_video_mp4_location else False
		if settings.autosave_video_custom:
			settings.output_file_serial_used = True if '{serial}' in settings.autosave_video_custom_location else False
	
	# If variable processing is turned on
	if prefs.render_variable_enable:
		# Save original output file path
		settings.output_file_path = filepath = scene.render.filepath
		# Check for serial number usage
		settings.output_file_serial_used = True if '{serial}' in scene.render.filepath else False
	
	# Save compositing node file paths if turned on in the plugin settings and compositing is enabled
	if prefs.render_variable_enable and scene.use_nodes:
		# Iterate through Compositor nodes, adding all file output node path and sub-path variables to a dictionary
		node_settings = {}
		for node in scene.node_tree.nodes:
			# Check if the node is a File Output node
			if isinstance(node, bpy.types.CompositorNodeOutputFile):
				# Save the base_path property and the file_slots dictionary entry
				node_settings[node.name] = {
					"base_path": node.base_path,
					"file_slots": {}
				}
				# Check for serial number usage
				settings.output_file_serial_used = True if '{serial}' in node.base_path else False
				
				# Save and then process the sub-path property of each file slot
				for i, slot in enumerate(node.file_slots):
					node_settings[node.name]["file_slots"][i] = {
						"path": slot.path
					}
					# Check for serial number usage
					settings.output_file_serial_used = True if '{serial}' in slot.path else False
					
		# Convert the dictionary to JSON format and save to the plugin preferences for safekeeping while rendering
		settings.output_file_nodes = json.dumps(node_settings)
	
	
	
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
