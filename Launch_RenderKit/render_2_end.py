# General features
import bpy
from bpy.app.handlers import persistent
import time
import json

# Local imports
from .render_variables import replaceVariables
from .utility_image import save_image
from .utility_log import save_log
from .utility_notifications import render_notifications

###########################################################################
# Post-render function
# •Reset render status variables
# •Reset output paths with original keywords
# •Autosave final rendered image
# •Send render complete alerts
# •Save log file

@persistent
def render_kit_end(scene):
	passed_scene = scene
	prefs = bpy.context.preferences.addons[__package__].preferences
	settings = scene.render_kit_settings
	
	# Set estimated render time and sequence status to false (render is complete or canceled, estimate display and FFmpeg check is no longer needed)
	settings.estimated_render_time_active = False
	settings.sequence_rendering_status = False
	# FFmpeg processing is handled in the render_kit_frame_post function (render_1_frame.py) in order to properly support timeline segmentation
	
	# Calculate elapsed render time and update total
	render_time = round(time.time() - float(settings.start_date), 2)
	settings.total_render_time = settings.total_render_time + render_time
	
	# If render variables are enabled, reset all output paths after rendering completes
	if prefs.render_variable_enable:
		
		# Restore unprocessed file path if processing is enabled
		if settings.output_file_path:
			scene.render.filepath = settings.output_file_path
			# Clear output file path storage
			settings.output_file_path = ""
	
		# Restore unprocessed node output file path if compositing is enabled and a file output node exists with the default node name
		compositing = scene.node_tree if bpy.app.version < tuple([5,0,0]) else scene.compositing_node_group
		if scene.render.use_compositing and compositing and len(settings.output_file_nodes) > 2:
			
			scene = bpy.context.scene
			
			# Get the JSON data from the preferences string where it was stashed
			json_data = settings.output_file_nodes
			
			# If the JSON data is not empty, deserialize it and restore the node settings
			if json_data:
				node_settings = json.loads(json_data)
				for node_name, node_data in node_settings.items():
					node = compositing.nodes.get(node_name)
					
					# Check if the node is a File Output node and unmuted
					if isinstance(node, bpy.types.CompositorNodeOutputFile) and not node.mute:
						if bpy.app.version < tuple([5,0,0]):
							# Reset base path
							node.base_path = node_data.get("directory", node.base_path)
						else:
							# Reset base path
							node.directory = node_data.get("directory", node.directory)
						
						# Get output port data
						output_port_data = node_data.get("outputs", {})
						for i, port_data in output_port_data.items():
							if bpy.app.version < tuple([5,0,0]):
								# --- Blender 4.x: restore from IDProperties on file_slots (existing behaviour) ---
								output_port = node.file_slots[int(i)]
								if output_port:
									# Reset slot path
									output_port.path = port_data.get("path", output_port.path)
							else:
								# --- Blender 5.x: restore from JSON for file_output_items ---
								output_item = node.file_output_items[int(i)]
								if output_item:
									if isinstance(port_data, dict):
										output_item.name = port_data.get("name", output_item.name)
									else:
										# Allow outputs to be stored as plain strings too
										# This should be revisited when 4.5 support is dropped
										output_item.name = port_data or output_item.name
			
			# Clear output node storage
			settings.output_file_nodes = ""
	
	# Timer function may be required to prevent render process conflicts in Blender 5.0
	# Currently disabled via "and False" and seems to be working ok (spinning it off into a timer might have been enough?)
	def _process_delay():
		if bpy.app.is_job_running('RENDER') and False:
			# Try again in 0.1 seconds
			return 0.1
		else:
			# Features that require the project to be saved
			if bpy.data.filepath:
				# Save external log file
				if prefs.external_log_file:
					save_log(render_time)
				
				# Autosave rendered image
				if prefs.enable_autosave_render:
					save_image(scene=passed_scene, render_time=render_time)
			
			# Render complete notifications
			if prefs.email_enable or prefs.pushover_enable or prefs.voice_enable:
				render_notifications(render_time)
			
			# Increment the output serial number if it was used in any output path
			# This must be done after all other steps are completed
			if settings.output_file_serial_used:
				settings.output_file_serial += 1
				settings.output_file_serial_used = False
		
		# Stop timer
		return None
	
	# Register timer function
	bpy.app.timers.register(_process_delay)
	
	return {'FINISHED'}