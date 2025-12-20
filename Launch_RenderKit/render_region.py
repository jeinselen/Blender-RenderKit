import bpy

class RENDER_PT_render_region(bpy.types.Panel):
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_context = "render"
	bl_parent_id = "RENDER_PT_format"
	bl_label = "Render Region Values"
	# bl_options = {'DEFAULT_CLOSED'}
	bl_options = {'HIDE_HEADER'}
	
	@classmethod
	def poll(cls, context):
		return bpy.context.scene.render.use_border and context.preferences.addons[__package__].preferences.region_enable
	
	# def draw_header(self, context):
		# self.layout.prop(bpy.context.scene.render, 'use_border', text='')
	
	def draw(self, context):
		layout = self.layout
		layout.use_property_decorate = False  # No animation
		layout.use_property_split = True
		
		row0 = layout.row(align=True, heading='')
		row0.prop(bpy.context.scene.render, 'border_min_x', text='Region X')
		row0.prop(bpy.context.scene.render, 'border_max_x', text='')
		row1 = layout.row(align=True, heading='')
		row1.prop(bpy.context.scene.render, 'border_min_y', text='Region Y')
		row1.prop(bpy.context.scene.render, 'border_max_y', text='')