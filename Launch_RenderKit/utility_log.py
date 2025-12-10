###########################################################################
# Process FFmpeg outputs

import bpy
import os
import subprocess
from re import sub

# Local imports
from .utility_time import secondsToReadable, readableToSeconds

def save_log(render_time=-1.0):
	prefs = bpy.context.preferences.addons[__package__].preferences
	
	# Get project name
	project_name = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
	
	# Log file settings
	logname = prefs.external_log_name
	logname = logname.replace("{{project}}", project_name)
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