###########################################################################
# Process FFmpeg outputs

import bpy
import os
import subprocess
from re import findall, M as multiline

# Local imports
from .render_variables import replaceVariables

# Image extension list (used when generating serial numbers based on existing files)
IMAGE_EXTENSIONS = (
	'jpg',
	'exr',
	'png',
	'webp',
	'bmp',
	'cin',
	'dpx',
	'rgb',
	'jp2',
	'hdr',
	'tga',
	'tif')

# Multilayer EXR files are not supported via the Python API - https://developer.blender.org/T71087

def save_image(scene, render_time=-1.0, serial=-1):
	context = bpy.context
	prefs = context.preferences.addons[__package__].preferences
	settings = scene.render_kit_settings
	
	# Get project name
	project_name = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
	
	# Save original file format settings
	original_format = scene.render.image_settings.file_format
	original_colormode = scene.render.image_settings.color_mode
	original_colordepth = scene.render.image_settings.color_depth
	
	# Set up render output formatting with override
	if prefs.override_autosave_render:
		file_format = prefs.file_format_global
	else:
		file_format = settings.file_format
	
	if file_format == 'JPEG':
		scene.render.image_settings.file_format = 'JPEG'
	elif file_format == 'PNG':
		scene.render.image_settings.file_format = 'PNG'
	elif file_format == 'OPEN_EXR':
		scene.render.image_settings.file_format = 'OPEN_EXR'
	
	extension = scene.render.file_extension
	
	# Get location variable with override and project path replacement
	if prefs.override_autosave_render:
		filepath = prefs.file_location_global
	else:
		filepath = settings.file_location
		
	# If the file path contains one or fewer characters, replace it with the project path
	if len(filepath) <= 1:
		filepath = os.path.join(os.path.dirname(bpy.data.filepath), project_name)
		
	# Convert relative path into absolute path for Python compatibility
	filepath = bpy.path.abspath(filepath)
	
	# Process elements that aren't available in the global variable replacement
	# The autosave serial number and override are separate from the project serial number
	serialUsedGlobal = False
	serialUsed = False
	serialNumber = -1
	if '{serial}' in filepath:
		if prefs.override_autosave_render:
			serialNumber = prefs.file_serial_global
			serialUsedGlobal = True
		else:
			serialNumber = settings.file_serial
			serialUsed = True
	
	# Replace global variables in the output path string
	filepath = replaceVariables(filepath, render_time=render_time, serial=serialNumber)
	
	# Create the project subfolder if it doesn't already exist (otherwise subsequent operations will fail)
	if not os.path.exists(filepath):
		os.makedirs(filepath)
	
	# Get file name type with override
	if prefs.override_autosave_render:
		file_name_type = prefs.file_name_type_global
	else:
		file_name_type = settings.file_name_type
	
	# Create the output file name string
	if file_name_type == 'SERIAL':
		# Generate dynamic serial number
		# Finds all of the image files that start with projectname in the selected directory
		files = [f for f in os.listdir(filepath) if f.startswith(project_name) and f.lower().endswith(IMAGE_EXTENSIONS)]
		
		# Searches the file collection and returns the next highest number as a 4 digit string
		def save_number_from_files(files):
			highest = -1
			if files:
				for f in files:
					# find filenames that end with four or more digits
					suffix = findall(r'\d{4,}$', os.path.splitext(f)[0].split(project_name)[-1], multiline)
					if suffix:
						if int(suffix[-1]) > highest:
							highest = int(suffix[-1])
			return format(highest+1, '04')
		
		# Create string with serial number
		filename = '{{project}}-' + save_number_from_files(files)
	elif file_name_type == 'DATE':
		filename = '{{project}} {{date}} {{time}}'
	elif file_name_type == 'RENDER':
		filename = '{{project}} {{engine}} {{duration}}'
	else:
		# Load custom file name with override
		if prefs.override_autosave_render:
			filename = prefs.file_name_custom_global
		else:
			filename = settings.file_name_custom
			
	if '{serial}' in filename:
		if prefs.override_autosave_render:
			serialNumber = prefs.file_serial_global
			serialUsedGlobal = True
		else:
			serialNumber = settings.file_serial
			serialUsed = True
			
	# Replace global variables in the output name string
	filename = replaceVariables(filename, render_time=render_time, serial=serialNumber)
	
	# Finish local and global serial number updates
	if serialUsedGlobal:
		prefs.file_serial_global += 1
	if serialUsed:
		settings.file_serial += 1
		
	# Combine file path and file name using system separator, add extension
	filepath = os.path.join(filepath, filename) + extension
	
	# Get rendered output
	image = next((img for img in bpy.data.images if img.type == 'RENDER_RESULT'), None)
	if not image or not image.has_data:
		image = bpy.data.images.get('Render Result')
	if not image or not image.has_data:
		image = bpy.data.images['Render Result']
	
#	if not image or not image.has_data:
#		print('Render Kit: Render Result not found. Image not saved.')
#		return {'CANCELLED'}
	
	# Check if directory exists
#	dir_path = os.path.dirname(filepath)
#	if not os.path.exists(dir_path):
#		print(f"  Directory does not exist, attempting to create: {dir_path}")
#		try:
#			os.makedirs(dir_path)
#		except Exception as e:
#			print(f"  Failed to create directory: {e}")
#			return {'CANCELLED'}
	
	# Check for invalid characters (beyond what's already sanitized)
#	if not filepath or len(filepath) == 0:
#		print("  Filepath is empty!")
#		return {'CANCELLED'}
	
	# Check if the filepath is too long (platform-specific limits)
#	if len(filepath) > 255:  # Most systems have a 255 character limit for filenames
#		print(f"  Filepath may be too long ({len(filepath)} characters)")
	
	if image and image.has_data:
		try:
			# Pass scene for correct color management
			image.save_render(filepath=filepath, scene=scene)
#			print("Image saved successfully")
		except Exception as e:
			print(f"ERROR - Failed to save image: {e}")
			import traceback
			traceback.print_exc()
	else:
		print("ERROR - No image data found")
		
	# Restore original user settings for render output
	scene.render.image_settings.file_format = original_format
	scene.render.image_settings.color_mode = original_colormode
	scene.render.image_settings.color_depth = original_colordepth