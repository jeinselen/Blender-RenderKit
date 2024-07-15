# General features
import bpy
#import json

# Email notifications
import smtplib
from email.mime.text import MIMEText

# Pushover notifications
import requests

# Command line voice access
import os



###########################################################################
# Notification system functions
# •Send email notification
# •Send Pushover notification

def send_email(subject, message):
	if bpy.app.online_access:
		prefs = bpy.context.preferences.addons[__package__].preferences
		try:
			msg = MIMEText(message)
			msg['Subject'] = subject
			msg['From'] = prefs.email_from
			msg['To'] = prefs.email_to
			with smtplib.SMTP_SSL(prefs.email_server, prefs.email_port) as smtp_server:
				smtp_server.login(prefs.email_from, prefs.email_password)
				smtp_server.sendmail(prefs.email_from, prefs.email_to.split(', '), msg.as_string())
		except Exception as exc:
			print(str(exc) + " | Error in Render Kit Notifications: failed to send email notification")



def send_pushover(subject, message):
	if bpy.app.online_access:
		prefs = bpy.context.preferences.addons[__package__].preferences
		try:
			r = requests.post('https://api.pushover.net/1/messages.json', data = {
				"token": prefs.pushover_app,
				"user": prefs.pushover_key,
				"title": subject,
				"message": message
			})
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
	try:
		os.system('say "' + message + '"')
	except Exception as exc:
		print(str(exc) + " | Error in Render Kit Notifications: failed to send Pushover notification")