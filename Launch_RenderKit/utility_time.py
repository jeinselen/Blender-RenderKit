###########################################################################
# Time conversion functions (because datetime doesn't like zero-numbered days or hours over 24)
# •Convert float seconds into string as [hour, minute, second] array (hours expand indefinitely, will not roll over into days)
# •Convert float seconds into string in HH:MM:SS.## format (hours expand indefinitely, will not roll over into days)
# •Convert string in HH:MM:SS.## format into float seconds

def secondsToStrings(sec):
	seconds, decimals = divmod(float(sec), 1)
	minutes, seconds = divmod(seconds, 60)
	hours, minutes = divmod(minutes, 60)
	return [
		"%d" % (hours),
		"%02d" % (minutes),
		"%02d.%02d" % (seconds, round(decimals*100))
	]

def secondsToReadable(seconds):
	h, m, s = secondsToStrings(seconds)
	return h + ":" + m + ":" + s

def readableToSeconds(readable):
	hours, minutes, seconds = readable.split(':')
	return int(hours)*3600 + int(minutes)*60 + float(seconds)
