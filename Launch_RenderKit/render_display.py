import bpy

# Internal imports
from .utility_time import secondsToReadable
from . import utility_data

###########################################################################
# Display total render time at the bottom of the Render tab > Output panel

class RENDER_PT_display_total_time(bpy.types.Panel):
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_context = "render"
	bl_parent_id = "RENDER_PT_output"
	bl_label = "Total Render Time"
	bl_options = {'HIDE_HEADER'}
	
	@classmethod
	def poll(cls, context):
		return context.preferences.addons[__package__].preferences.show_total_render_time
	
	# def draw_header(self, context):
		# self.layout.prop(bpy.context.scene.render, 'use_border', text='')
	
	def draw(self, context):
		layout = self.layout
		layout.use_property_decorate = False  # No animation
		layout.use_property_split = True
		layout.label(text=" Total scene render time: "+secondsToReadable(context.scene.render_kit_settings.total_render_time))



###########################################################################
# Display estimated time remaining in the Image viewer during rendering

def RenderKit_display_time_remaining(self, context):
	context = bpy.context
#	prefs = context.preferences.addons[__package__].preferences
#	settings = context.scene.render_kit_settings
	
#	if not (False) and context.preferences.addons[__package__].preferences.show_estimated_render_time and settings.sequence_active:
	if not (False) and context.preferences.addons[__package__].preferences.show_estimated_render_time and utility_data.render_get_sequence():
		layout = self.layout
#		layout.separator()
		layout.label(text=str(secondsToReadable(utility_data.render_get_estimate())), icon='TIME') # TIME SORTTIME MOD_TIME
	else:
		pass



###########################################################################
# Addon registration functions
# •Define classes being registered
# •Registration function
# •Unregistration function

#classes = (RENDER_PT_display_total_time, RENDERKIT_MT_display_time_remaining)
classes = (RENDER_PT_display_total_time,)
#classes = (RENDERKIT_MT_display_time_remaining,)

def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)
	
#	bpy.types.RENDER_PT_output.append(RENDER_PT_total_render_time_display)
#	bpy.types.IMAGE_HT_header.append(RenderKit_display_time_remaining)
	bpy.types.IMAGE_MT_editor_menus.append(RenderKit_display_time_remaining)

def unregister():
#	bpy.types.RENDER_PT_output.remove(RENDER_PT_total_render_time_display)
#	bpy.types.IMAGE_HT_header.remove(RenderKit_display_time_remaining)
	bpy.types.IMAGE_MT_editor_menus.remove(RenderKit_display_time_remaining)
	
	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)


if __package__ == "__main__":
	register()