###########################################################################
# Process FFmpeg outputs

import bpy
import os
import subprocess
from re import sub

# Local imports
from .render_variables import replaceVariables

FFMPEG_FORMATS = (
	'BMP',
	'PNG',
	'JPEG',
	'DPX',
	'OPEN_EXR',
	'TIFF')

def processFFmpeg(render_path='', render_time=-1):
	context = bpy.context
	prefs = context.preferences.addons[__package__].preferences
	scene = context.scene
	settings = scene.render_kit_settings
	format_compatible = True if scene.render.image_settings.file_format in FFMPEG_FORMATS else False
	
	# Output video files if FFmpeg processing is enabled, the command appears to exist, and the image format output is supported
	if prefs.ffmpeg_processing and prefs.ffmpeg_exists and format_compatible:
		# Create initial command base
		ffmpeg_location = prefs.ffmpeg_location
		
		# Create absolute path and strip trailing spaces
		if not render_path:
			render_path = scene.render.filepath
		absolute_path = bpy.path.abspath(render_path).rstrip()
		
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
#			settings.autosave_video_sequence_processing = True
			if len(settings.autosave_video_prores_location) > 1:
				# Replace with custom string
				output_path = settings.autosave_video_prores_location
				# Replace dynamic variables
				settings.output_file_serial_used = True if '{serial}' in output_path else False
				output_path = replaceVariables(output_path, render_time=render_time, serial=settings.output_file_serial)
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
				subprocess.Popen(ffmpeg_command, shell=True)
				print('')
			except Exception as exc:
				print(str(exc) + " | Error in Render Kit: failed to process FFmpeg ProRes command")
		
		# MP4 output
		if settings.autosave_video_mp4:
			# Set FFmpeg processing to true so the Image View window can display status
#			settings.autosave_video_sequence_processing = True
			if len(settings.autosave_video_mp4_location) > 1:
				# Replace with custom string
				output_path = settings.autosave_video_mp4_location
				# Replace dynamic variables
				settings.output_file_serial_used = True if '{serial}' in output_path else False
				output_path = replaceVariables(output_path, render_time=render_time, serial=settings.output_file_serial)
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
				subprocess.Popen(ffmpeg_command, shell=True)
				print('')
			except Exception as exc:
				print(str(exc) + " | Error in Render Kit: failed to process FFmpeg MP4 command")
		
		# Custom output
		if settings.autosave_video_custom:
			# Set FFmpeg processing to true so the Image View window can display status
#			settings.autosave_video_sequence_processing = True
			if len(settings.autosave_video_custom_location) > 1:
				# Replace with custom string
				output_path = settings.autosave_video_custom_location
				# Replace dynamic variables
				settings.output_file_serial_used = True if '{serial}' in output_path else False
				output_path = replaceVariables(output_path, render_time=render_time, serial=settings.output_file_serial)
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
				subprocess.Popen(ffmpeg_command, shell=True)
				print('')
			except Exception as exc:
				print(str(exc) + " | Error in Render Kit: failed to process FFmpeg custom command")
	
	else:
		print("Error in Render Kit: FFmpeg check failed, the output image format may not be compatible")
		