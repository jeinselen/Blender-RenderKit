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
	settings.autosave_video_sequence = False
	
	# Track usage of the output serial usage globally to ensure it can be accessed before/after rendering
	# Set it to false ahead of processing to ensure no errors occur (usually only if there's a crash of some sort)
	settings.output_file_serial_used = False
	
	# Filter output file path if enabled
	if prefs.render_output_variables:
		# Save original file path
		settings.output_file_path = filepath = scene.render.filepath
		
		# Check if the serial variable is used
		settings.output_file_serial_used = True if '{serial}' in filepath else False
		
		# Replace scene filepath output with the processed version
		scene.render.filepath = replaceVariables(filepath, serial=settings.output_file_serial)
	
	# Reset autosave video output path tracking
	settings.autosave_video_render_path = ''
	
	# Filter compositing node file path if turned on in the plugin settings and compositing is enabled
	if prefs.render_output_variables and scene.use_nodes:
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
				# Replace dynamic variables
				settings.output_file_serial_used = True if '{serial}' in node.base_path else False
				node.base_path = replaceVariables(node.base_path, serial=settings.output_file_serial)
				
				# Save and then process the sub-path property of each file slot
				for i, slot in enumerate(node.file_slots):
					node_settings[node.name]["file_slots"][i] = {
						"path": slot.path
					}
					# Replace dynamic variables
					settings.output_file_serial_used = True if '{serial}' in slot.path else False
					slot.path = replaceVariables(slot.path, serial=settings.output_file_serial)
		
		# Convert the dictionary to JSON format and save to the plugin preferences for safekeeping while rendering
		settings.output_file_nodes = json.dumps(node_settings)
