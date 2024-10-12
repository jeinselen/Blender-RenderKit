# General features
import bpy
from bpy.app.handlers import persistent
import time
import json

# File paths
import os

# Variable data
from re import findall, sub, M as multiline

# FFmpeg system access
import subprocess

# Local imports
from .render_variables import replaceVariables
from .utility_notifications import render_notifications
from .utility_time import secondsToReadable, readableToSeconds

# Format validation lists
IMAGE_FORMATS = (
	'BMP',
	'IRIS',
	'PNG',
	'JPEG',
	'JPEG2000',
	'TARGA',
	'TARGA_RAW',
	'CINEON',
	'DPX',
	'OPEN_EXR_MULTILAYER',
	'OPEN_EXR',
	'HDR',
	'TIFF')

IMAGE_EXTENSIONS = (
	'bmp',
	'rgb',
	'png',
	'jpg',
	'jp2',
	'tga',
	'cin',
	'dpx',
	'exr',
	'hdr',
	'tif')

FFMPEG_FORMATS = (
	'BMP',
	'PNG',
	'JPEG',
	'DPX',
	'OPEN_EXR',
	'TIFF')



###########################################################################
# Post-render function
# •Compile output video using FFmpeg
# •Autosave final rendered image
# •Reset render status variables
# •Reset output paths with original keywords
# •Send render complete alerts
# •Save log file

@persistent
def render_kit_end(scene):
	prefs = bpy.context.preferences.addons[__package__].preferences
	settings = bpy.context.scene.render_kit_settings
	
	# Set estimated render time active to false (render is complete or canceled, estimate display and FFmpeg check is no longer needed)
	settings.estimated_render_time_active = False
	
	# Calculate elapsed render time
	render_time = round(time.time() - float(settings.start_date), 2)
	
	# Update total render time
	settings.total_render_time = settings.total_render_time + render_time
	
	# Output video files if FFmpeg processing is enabled, the command appears to exist, and the image format output is supported
	if prefs.ffmpeg_processing and prefs.ffmpeg_exists and bpy.context.scene.render.image_settings.file_format in FFMPEG_FORMATS and settings.autosave_video_sequence:
		# Create initial command base
		ffmpeg_location = prefs.ffmpeg_location
		# Create absolute path and strip trailing spaces
		absolute_path = bpy.path.abspath(scene.render.filepath).rstrip()
		# Replace frame number placeholder with asterisk or add trailing asterisk
		if "#" in absolute_path:
			absolute_path = sub(r'#+(?!.*#)', "*", absolute_path)
		else:
			absolute_path += "*"
		# Create input image glob pattern
		glob_pattern = '-pattern_type glob -i "' + absolute_path + scene.render.file_extension + '"'
		# Create floating point FPS value
		fps_float = '-r ' + str(scene.render.fps / scene.render.fps_base)
		
		# ProRes output
		if settings.autosave_video_prores:
			# Set FFmpeg processing to true so the Image View window can display status
			settings.autosave_video_sequence_processing = True
			if len(settings.autosave_video_prores_location) > 1:
				# Replace with custom string
				output_path = settings.autosave_video_prores_location
				# Replace dynamic variables
				if '{serial}' in output_path:
					settings.output_file_serial_used = True
				output_path = replaceVariables(output_path, rendertime=render_time, serial=settings.output_file_serial)
				# Convert relative path into absolute path for Python and CLI compatibility
				output_path = bpy.path.abspath(output_path)
				# Create the project subfolder if it doesn't already exist
				output_dir = sub(r'[^/]*$', "", output_path)
				if not os.path.exists(output_dir):
					os.makedirs(output_dir)
				# Wrap with FFmpeg settings
				output_path = '-y "' + output_path + '"'
			
			# FFmpeg location
			ffmpeg_command = ffmpeg_location
			# Frame rate
			ffmpeg_command += ' ' + fps_float
			# Image sequence pattern
			ffmpeg_command += ' ' + glob_pattern
			# ProRes format
			ffmpeg_command += ' -c:v prores -pix_fmt yuv422p10le'
			# ProRes profile (Proxy, LT, 422 HQ)
			ffmpeg_command += ' -profile:v ' + str(settings.autosave_video_prores_quality)
			# Final output settings
			ffmpeg_command += ' -vendor apl0 -an -sn'
			# Output file path
			ffmpeg_command += ' ' + output_path + '.mov'
			# Remove any accidental double spaces
			ffmpeg_command = sub(r'\s{2,}', " ", ffmpeg_command)
			
			# Print command to the terminal
			print('FFmpeg ProRes command:')
			print(ffmpeg_command)
			print('')
			
			# Run FFmpeg command
			try:
				subprocess.call(ffmpeg_command, shell=True)
				print('')
			except Exception as exc:
				print(str(exc) + " | Error in Render Kit: failed to process FFmpeg ProRes command")
		
		# MP4 output
		if settings.autosave_video_mp4:
			# Set FFmpeg processing to true so the Image View window can display status
			settings.autosave_video_sequence_processing = True
			if len(settings.autosave_video_mp4_location) > 1:
				# Replace with custom string
				output_path = settings.autosave_video_mp4_location
				# Replace dynamic variables
				if '{serial}' in output_path:
					settings.output_file_serial_used = True
				output_path = replaceVariables(output_path, rendertime=render_time, serial=settings.output_file_serial)
				# Convert relative path into absolute path for Python and CLI compatibility
				output_path = bpy.path.abspath(output_path)
				# Create the project subfolder if it doesn't already exist
				output_dir = sub(r'[^/]*$', "", output_path)
				if not os.path.exists(output_dir):
					os.makedirs(output_dir)
				# Wrap with FFmpeg settings
				output_path = '-y "' + output_path + '"'
			
			# FFmpeg location
			ffmpeg_command = ffmpeg_location
			# Frame rate
			ffmpeg_command += ' ' + fps_float
			# Image sequence pattern
			ffmpeg_command += ' ' + glob_pattern
			# MP4 format
			ffmpeg_command += ' -c:v libx264 -preset slow'
			# MP4 quality (0-51 from highest to lowest quality)
			ffmpeg_command += ' -crf ' + str(settings.autosave_video_mp4_quality)
			# Final output settings
			ffmpeg_command += ' -pix_fmt yuv420p -movflags rtphint'
			# Output file path
			ffmpeg_command += ' ' + output_path + '.mp4'
			# Remove any accidental double or more spaces
			ffmpeg_command = sub(r'\s{2,}', " ", ffmpeg_command)
			
			# Print command to the terminal
			print('FFmpeg MP4 command:')
			print(ffmpeg_command)
			print('')
			
			# Run FFmpeg command
			try:
				subprocess.call(ffmpeg_command, shell=True)
				print('')
			except Exception as exc:
				print(str(exc) + " | Error in Render Kit: failed to process FFmpeg MP4 command")
		
		# Custom output
		if settings.autosave_video_custom:
			# Set FFmpeg processing to true so the Image View window can display status
			settings.autosave_video_sequence_processing = True
			if len(settings.autosave_video_custom_location) > 1:
				# Replace with custom string
				output_path = settings.autosave_video_custom_location
				# Replace dynamic variables
				if '{serial}' in output_path:
					settings.output_file_serial_used = True
				output_path = replaceVariables(output_path, rendertime=render_time, serial=settings.output_file_serial)
				# Convert relative path into absolute path for Python and CLI compatibility
				output_path = bpy.path.abspath(output_path)
				# Create the project subfolder if it doesn't already exist
				output_dir = sub(r'[^/]*$', "", output_path)
				if not os.path.exists(output_dir):
					os.makedirs(output_dir)
				# Wrap with FFmpeg settings
				output_path = '-y "' + output_path + '"'
			
			# FFmpeg location
			ffmpeg_command = ffmpeg_location + ' ' + settings.autosave_video_custom_command
			# Replace variables
			ffmpeg_command = ffmpeg_command.replace("{fps}", fps_float)
			ffmpeg_command = ffmpeg_command.replace("{input}", glob_pattern)
			ffmpeg_command = ffmpeg_command.replace("{output}", output_path)
			# Remove any accidental double spaces
			ffmpeg_command = sub(r'\s{2,}', " ", ffmpeg_command)
			
			# Print command to the terminal
			print('FFmpeg custom command:')
			print(ffmpeg_command)
			print('')
			
			# Run FFmpeg command
			try:
				subprocess.call(ffmpeg_command, shell=True)
				print('')
			except Exception as exc:
				print(str(exc) + " | Error in Render Kit: failed to process FFmpeg custom command")
	
	# Increment the output serial number if it was used in any output path
	if settings.output_file_serial_used:
		settings.output_file_serial += 1
		settings.output_file_serial_used = False
	
	# Set video sequence status to false
	settings.autosave_video_sequence = False
	settings.autosave_video_sequence_processing = False
	
	# Restore unprocessed file path if processing is enabled
	if prefs.render_output_variables and settings.output_file_path:
		scene.render.filepath = settings.output_file_path
	
	# Restore unprocessed node output file path if processing is enabled, compositing is enabled, and a file output node exists with the default node name
	if prefs.render_output_variables and bpy.context.scene.use_nodes and len(settings.output_file_nodes) > 2:
		
		# Get the JSON data from the preferences string where it was stashed
		json_data = settings.output_file_nodes
		
		# If the JSON data is not empty, deserialize it and restore the node settings
		if json_data:
			node_settings = json.loads(json_data)
			for node_name, node_data in node_settings.items():
				node = bpy.context.scene.node_tree.nodes.get(node_name)
				if isinstance(node, bpy.types.CompositorNodeOutputFile):
					node.base_path = node_data.get("base_path", node.base_path)
					file_slots_data = node_data.get("file_slots", {})
					for i, slot_data in file_slots_data.items():
						slot = node.file_slots[int(i)]
						if slot:
							slot.path = slot_data.get("path", slot.path)
	
	# Get project name (used by both autosave render and the external log file)
	projectname = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
	
	# Autosave render
	if (prefs.enable_autosave_render) and bpy.data.filepath:
		
		# Save original file format settings
		original_format = scene.render.image_settings.file_format
		original_colormode = scene.render.image_settings.color_mode
		original_colordepth = scene.render.image_settings.color_depth
		
		# Set up render output formatting with override
		if prefs.file_format_override:
			file_format = prefs.file_format_global
		else:
			file_format = settings.file_format
		
		if file_format == 'SCENE':
			if original_format not in IMAGE_FORMATS:
				print('Render Kit: {} is not an image format. Image not saved.'.format(original_format))
				return {'CANCELLED'}
		elif file_format == 'JPEG':
			scene.render.image_settings.file_format = 'JPEG'
		elif file_format == 'PNG':
			scene.render.image_settings.file_format = 'PNG'
		elif file_format == 'OPEN_EXR':
			scene.render.image_settings.file_format = 'OPEN_EXR'
		extension = scene.render.file_extension
		
		# Get location variable with override and project path replacement
		if prefs.file_location_override:
			filepath = prefs.file_location_global
		else:
			filepath = settings.file_location
		
		# If the file path contains one or fewer characters, replace it with the project path
		if len(filepath) <= 1:
			filepath = os.path.join(os.path.dirname(bpy.data.filepath), projectname)
		
		# Convert relative path into absolute path for Python compatibility
		filepath = bpy.path.abspath(filepath)
		
		# Process elements that aren't available in the global variable replacement
		# The autosave serial number and override are separate from the project serial number
		serialUsedGlobal = False
		serialUsed = False
		serialNumber = -1
		if '{serial}' in filepath:
			if prefs.file_location_override:
				serialNumber = prefs.file_serial_global
				serialUsedGlobal = True
			else:
				serialNumber = settings.file_serial
				serialUsed = True
		
		# Replace global variables in the output path string
		filepath = replaceVariables(filepath, rendertime=render_time, serial=serialNumber)
		
		# Create the project subfolder if it doesn't already exist (otherwise subsequent operations will fail)
		if not os.path.exists(filepath):
			os.makedirs(filepath)
		
		# Get file name type with override
		if prefs.file_name_override:
			file_name_type = prefs.file_name_type_global
		else:
			file_name_type = settings.file_name_type
		
		# Create the output file name string
		if file_name_type == 'SERIAL':
			# Generate dynamic serial number
			# Finds all of the image files that start with projectname in the selected directory
			files = [f for f in os.listdir(filepath) if f.startswith(projectname) and f.lower().endswith(IMAGE_EXTENSIONS)]
			
			# Searches the file collection and returns the next highest number as a 4 digit string
			def save_number_from_files(files):
				highest = -1
				if files:
					for f in files:
						# find filenames that end with four or more digits
						suffix = findall(r'\d{4,}$', os.path.splitext(f)[0].split(projectname)[-1], multiline)
						if suffix:
							if int(suffix[-1]) > highest:
								highest = int(suffix[-1])
				return format(highest+1, '04')
			
			# Create string with serial number
			filename = '{project}-' + save_number_from_files(files)
		elif file_name_type == 'DATE':
			filename = '{project} {date} {time}'
		elif file_name_type == 'RENDER':
			filename = '{project} {engine} {duration}'
		else:
			# Load custom file name with override
			if prefs.file_name_override:
				filename = prefs.file_name_custom_global
			else:
				filename = settings.file_name_custom
		
		if '{serial}' in filename:
			if prefs.file_location_override:
				serialNumber = prefs.file_serial_global
				serialUsedGlobal = True
			else:
				serialNumber = settings.file_serial
				serialUsed = True
		
		# Replace global variables in the output name string
		filename = replaceVariables(filename, rendertime=render_time, serial=serialNumber)
		
		# Finish local and global serial number updates
		if serialUsedGlobal:
			prefs.file_serial_global += 1
		if serialUsed:
			settings.file_serial += 1
		
		# Combine file path and file name using system separator, add extension
		filepath = os.path.join(filepath, filename) + extension
		
		# Save image file
		image = bpy.data.images['Render Result']
		if not image:
			print('Render Kit: Render Result not found. Image not saved.')
			return {'CANCELLED'}
		
		# Please note that multilayer EXR files are currently unsupported in the Python API - https://developer.blender.org/T71087
		image.save_render(filepath, scene=None) # Consider using bpy.context.scene if different compression settings are desired per-scene
		
		# Restore original user settings for render output
		scene.render.image_settings.file_format = original_format
		scene.render.image_settings.color_mode = original_colormode
		scene.render.image_settings.color_depth = original_colordepth
	
	# Render complete notifications
	render_notifications(render_time)
	
	# Save external log file
	if prefs.external_render_time:
		# Log file settings
		logname = prefs.external_log_name
		logname = logname.replace("{project}", projectname)
		logpath = os.path.join(os.path.dirname(bpy.data.filepath), logname) # Limited to locations local to the project file
		logtitle = 'Total Render Time: '
		logtime = 0.00
		
		# Get previous time spent rendering, if log file exists, and convert formatted string into seconds
		if os.path.exists(logpath):
			with open(logpath) as filein:
				logtime = filein.read().replace(logtitle, '')
				logtime = readableToSeconds(logtime)
		# Create log file directory location if it doesn't exist
		elif not os.path.exists(os.path.dirname(logpath)): # Safety net just in case a folder was included in the file name entry
			os.makedirs(os.path.dirname(logpath))
		
		# Add the latest render time
		logtime += float(render_time)
		
		# Convert seconds into formatted string
		logtime = secondsToReadable(logtime)
		
		# Write log file
		with open(logpath, 'w') as fileout:
			fileout.write(logtitle + logtime)
	
	return {'FINISHED'}
