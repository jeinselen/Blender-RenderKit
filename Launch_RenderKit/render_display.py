import bpy
from .utility_time import secondsToReadable

###########################################################################
# Display total render time at the bottom of the Render tab > Output panel

def RENDER_PT_total_render_time_display(self, context):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	
	if not (False) and prefs.show_total_render_time:
		layout = self.layout
		box = layout.box()
		box.label(text="Total time spent rendering: "+secondsToReadable(settings.total_render_time))



###########################################################################
# Display estimated time remaining in the Image viewer during rendering

def image_viewer_feedback_display(self, context):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	
	if prefs.show_estimated_render_time and settings.sequence_rendering_status:
		self.layout.separator()
		box = self.layout.box()
		box.label(text="  Estimated Time Remaining: " + settings.estimated_render_time_value + " ")
