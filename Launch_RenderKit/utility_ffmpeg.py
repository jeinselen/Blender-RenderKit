###########################################################################
# Process FFmpeg outputs

import bpy
import os
import subprocess
from re import sub

# Local imports
from .render_variables import replaceVariables
from . import utility_data

FFMPEG_FORMATS = (
	'BMP',
	'PNG',
	'JPEG',
	'DPX',
	'OPEN_EXR',
	'TIFF')

def process_ffmpeg(scene, render_path='', render_time=-1):
	# Use preferences cached at render_init to avoid bpy.context access mid-render;
	# process_ffmpeg is called from the render_post handler during the render loop
	prefs = utility_data.get_prefs()
	if prefs is None:
		prefs = bpy.context.preferences.addons[__package__].preferences
#	scene = bpy.context.scene
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
		
		# Input image glob pattern (passed to FFmpeg as a single argument)
		input_glob = absolute_path + scene.render.file_extension
		
		# Floating point FPS value
		fps_value = str(scene.render.fps / scene.render.fps_base)
		
		# Resolve a configured output template to an absolute path and ensure its folder exists
		# Returns None when the template is effectively empty (nothing to write)
		def prepare_output_path(path_template):
			if len(path_template) <= 1:
				return None
			# Replace dynamic variables
			output_path = replaceVariables(scene, path_template, render_time=render_time)
			# Convert relative path into absolute path for Python and CLI compatibility
			output_path = bpy.path.abspath(output_path)
			# Create the project subfolder if it doesn't already exist
			output_dir = sub(r'[^/]*$', "", output_path)
			if output_dir and not os.path.exists(output_dir):
				os.makedirs(output_dir)
			return output_path
		
		# ProRes output
		if settings.autosave_video_prores:
			output_path = prepare_output_path(settings.autosave_video_prores_path)
			if output_path is not None:
				ffmpeg_command = [
					ffmpeg_location,
					'-r', fps_value,
					'-pattern_type', 'glob', '-i', input_glob,
					'-c:v', 'prores', '-pix_fmt', 'yuv422p10le',
					# ProRes profile (Proxy, LT, 422 HQ)
					'-profile:v', str(settings.autosave_video_prores_quality),
					'-vendor', 'apl0', '-an', '-sn',
					'-y', output_path + '.mov',
				]
				
				# Print command to the terminal
				print(f'FFmpeg ProRes command: {ffmpeg_command}')
				
				# Run FFmpeg command (no shell)
				try:
					subprocess.Popen(ffmpeg_command)
				except Exception as exc:
					print(str(exc) + " | Error in Render Kit: failed to process FFmpeg ProRes command")
		
		# MP4 output
		if settings.autosave_video_mp4:
			output_path = prepare_output_path(settings.autosave_video_mp4_path)
			if output_path is not None:
				ffmpeg_command = [
					ffmpeg_location,
					'-r', fps_value,
					'-pattern_type', 'glob', '-i', input_glob,
					'-c:v', 'libx264', '-preset', 'slow',
					# MP4 quality (0-51 from highest to lowest quality)
					'-crf', str(settings.autosave_video_mp4_quality),
					'-pix_fmt', 'yuv420p', '-movflags', 'rtphint',
					'-y', output_path + '.mp4',
				]
				
				# Print command to the terminal
				print(f'FFmpeg MP4 command: {ffmpeg_command}')
				
				# Run FFmpeg command (no shell)
				try:
					subprocess.Popen(ffmpeg_command)
				except Exception as exc:
					print(str(exc) + " | Error in Render Kit: failed to process FFmpeg MP4 command")
		
		# Custom argument list
		if settings.autosave_video_custom:
			custom_command = settings.autosave_video_custom_command
			output_path = prepare_output_path(settings.autosave_video_custom_path)
			if output_path is None:
				pass
			elif "{{output}}" not in custom_command:
				# Without an {{output}} placeholder there is no destination to write to; skip rather than run a command that produces nothing
				print("Error in Render Kit: FFmpeg custom command has no {{output}} placeholder, skipping")
			else:
				# FFmpeg location, then the user template split into arguments with placeholders expanded
				ffmpeg_command = [ffmpeg_location]
				# Match placeholders as substrings so affixes (e.g. {{output}}_alpha.mov) are preserved,
				# while keeping each argument as a separate list item to avoid shell interpretation
				for token in custom_command.split():
					if "{{fps}}" in token:
						ffmpeg_command += ['-r', token.replace("{{fps}}", fps_value)]
					elif "{{input}}" in token:
						ffmpeg_command += ['-pattern_type', 'glob', '-i', token.replace("{{input}}", input_glob)]
					elif "{{output}}" in token:
						ffmpeg_command += ['-y', token.replace("{{output}}", output_path)]
					else:
						ffmpeg_command.append(token)
				
				# Print command to the terminal
				print(f'FFmpeg custom command: {ffmpeg_command}')
				
				# Run FFmpeg command (no shell)
				try:
					subprocess.Popen(ffmpeg_command)
				except Exception as exc:
					print(str(exc) + " | Error in Render Kit: failed to process FFmpeg custom command")
	else:
		print("Error in Render Kit: FFmpeg check failed, the output image format may not be compatible")
		