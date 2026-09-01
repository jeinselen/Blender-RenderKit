# General features
import bpy
import threading

# Pushover notifications
import requests

# Command line voice access
import subprocess

# Variables
from .render_variables import replaceVariables

# Network timeout (seconds) for notification services so that slow or unreachable
# servers don't block the sending thread indefinitely
NOTIFICATION_TIMEOUT = 20



###########################################################################
# Notification system functions
# •Send Pushover notification
# •Speak audible message
#
# Message strings are built on the main thread (replaceVariables reads bpy data)
# Blocking network sends run on a separate thread so network issues don't freeze Blender

def render_notifications(scene, render_time=-1.0):
	prefs = bpy.context.preferences.addons[__package__].preferences
	
	if render_time > float(prefs.minimum_time):
		
		# Build and send Pushover notification (snapshot preference values on the main thread)
		# The bpy.app.online_access gate is REQUIRED by Blender's extension policy
		if bpy.app.online_access and prefs.pushover_enable and len(prefs.pushover_key) == 30 and len(prefs.pushover_app) == 30:
			pushover_config = {
				"app": prefs.pushover_app,
				"key": prefs.pushover_key,
				"subject": replaceVariables(scene, prefs.pushover_subject, render_time=render_time),
				"message": replaceVariables(scene, prefs.pushover_message, render_time=render_time),
			}
			threading.Thread(target=send_pushover, args=(pushover_config,), daemon=True).start()
		
		# MacOS Siri text-to-speech announcement (non-blocking via Popen)
		# Re-check voice location just to be extra-sure (otherwise this is only checked when the add-on is first enabled)
		prefs.check_voice_location()
		if prefs.voice_exists and prefs.voice_enable:
			message = replaceVariables(scene, prefs.voice_message, render_time=render_time)
			voice_say(message)



# Operates on the plain-Python config snapshot not bpy data
def send_pushover(config):
	try:
		r = requests.post('https://api.pushover.net/1/messages.json', data = {
			"token": config["app"],
			"user": config["key"],
			"title": config["subject"],
			"message": config["message"]
		}, timeout=NOTIFICATION_TIMEOUT)
		if r.status_code == 200:
			print(r.text)
		if r.status_code == 500:
			print('Error in Render Kit Notifications: Pushover notification service unavailable')
			print(r.text)
		else:
			print('Error in Render Kit Notifications: Pushover URL request failed')
			print(r.text)
	except Exception as exc:
		print(str(exc) + " | Error in Render Kit Notifications: failed to send Pushover notification")



def voice_say(message):
	# This can be expanded to support other systems if needed, but right now it's MacOS exclusive
	# Pass arguments as a list (no shell) so the message can't break the command or freeze Blender
	try:
		subprocess.Popen(['say', message])
	except Exception as exc:
		print(str(exc) + " | Error in Render Kit Notifications: failed to announce voice notification")
