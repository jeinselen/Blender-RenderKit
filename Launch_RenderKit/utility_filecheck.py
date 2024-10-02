###########################################################################
# Check Existing And Increment
# •Returns plain text path in the same format as delivered
# •Check for existing directory using absolute path; if it doesn't exist, create it
# •Check for existing file in same location; if it exists, return modified file name with serial number

import bpy
import os
from re import search

def checkExistingAndIncrement(path, overwrite=False):
	abs_path = bpy.path.abspath(path)
	abs_dir, abs_name = os.path.split(abs_path)
	abs_name, abs_ext = os.path.splitext(abs_name)
	
	if not os.path.exists(abs_dir):
		os.makedirs(abs_dir)
	elif os.path.isfile(abs_path) and not overwrite:
		# If the file exists, determine the correct serial number to increment
		serial = -1
		for file in os.listdir(abs_dir):
			if file.startswith(abs_name):
				# If incremented files exist, continue to increment
				('check file: ', file)
				match = search(r'(\d+)(?=\D*$)', file)
				if match:
					serial = max(serial, int(match.group(1)))
		
		# Deconstruct file path
		path, ext = os.path.splitext(path)
		
		# Reconstruct file path with serial number
		path += '-' + format(serial+1, '04') + ext
	
	return path
