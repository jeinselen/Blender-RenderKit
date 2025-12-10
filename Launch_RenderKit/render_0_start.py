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
	compositing = scene.node_tree if bpy.app.version < tuple([5,0,0]) else scene.compositing_node_group
	
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
	if prefs.render_variable_enable and scene.render.use_compositing and compositing:
		# Iterate through Compositor nodes, adding all file output node path and sub-path variables to a dictionary
		node_settings = {}
		for node in compositing.nodes:
			# Check if the node is a File Output node
			if isinstance(node, bpy.types.CompositorNodeOutputFile):
				directory = node.base_path if bpy.app.version < tuple([5,0,0]) else node.directory
				
				# Save the directory property and the output items dictionary entry
				node_settings[node.name] = {
					"directory": directory,
					"outputs": {}
				}
				# Check for serial number usage
				settings.output_file_serial_used = True if '{serial}' in directory else False
				
				# Save and then process the sub-path property of each file port
				output_ports = node.file_slots if bpy.app.version < tuple([5,0,0]) else node.file_output_items
				for i, output_port in enumerate(output_ports):
					if bpy.app.version < tuple([5,0,0]):
						node_settings[node.name]["outputs"][i] = {
							"path": output_port.path
						}
						# Check for serial number usage
						settings.output_file_serial_used = True if '{serial}' in output_port.path else False
					else:
						node_settings[node.name]["outputs"][i] = {
							"name": output_port.name
						}
						# Check for serial number usage
						settings.output_file_serial_used = True if '{serial}' in output_port.name else False
					
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
		if scene.render.use_compositing and compositing and settings.output_file_nodes:
			# Get the JSON data from the preferences string where it was stashed
			json_data = settings.output_file_nodes
			
			# If the JSON data is not empty, deserialize it and update the string values with new variables
			if json_data:
				node_settings = json.loads(json_data)
				
				# Get node data
				for node_name, node_data in node_settings.items():
					node = compositing.nodes.get(node_name)
					if isinstance(node, bpy.types.CompositorNodeOutputFile):
						if bpy.app.version < tuple([5,0,0]):
							# Reset base path
							node.base_path = node_data.get("directory", node.base_path)
							# Replace dynamic variables in the base path
							node.base_path = replaceVariables(node.base_path)
						else:
							# Reset base path
							node.directory = node_data.get("directory", node.directory)
							# Replace dynamic variables in the base path
							node.directory = replaceVariables(node.directory)
						
						# Get output port data
						output_port_data = node_data.get("outputs", {})
						for i, port_data in output_port_data.items():
							if bpy.app.version < tuple([5,0,0]):
								output_port = node.file_slots[int(i)]
								if output_port:
									# Reset slot path
									output_port.path = port_data.get("path", output_port.path)
									# Replace dynamic variables in the slot path
									output_port.path = replaceVariables(output_port.path)
							else:
								output_port = node.file_output_items[int(i)]
								if output_port:
									# Reset port path
									output_port.name = port_data.get("name", output_port.name)
									# Replace dynamic variables in the output port path
									output_port.name = replaceVariables(output_port.name)
								