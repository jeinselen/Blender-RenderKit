import bpy
from .render_variables import renderkit_variable_ui

# Format validation list
FFMPEG_FORMATS = (
	'BMP',
	'PNG',
	'JPEG',
	'DPX',
	'OPEN_EXR',
	'TIFF')



###########################################################################
# Output Properties panel UI rendering classes
# •Autosave Images panel
# •Autosave Videos panel

class RENDER_PT_autosave_image(bpy.types.Panel):
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_context = "output"
	bl_label = "Autosave Images"
	bl_parent_id = "RENDER_PT_output"
	
	@classmethod
	def poll(cls, context):
		prefs = context.preferences.addons[__package__].preferences
		return (
			# Check if autosaving images is enabled
			prefs.enable_autosave_render
		)
	
	def draw(self, context):
		prefs = context.preferences.addons[__package__].preferences
		settings = context.scene.render_kit_settings
		
		layout = self.layout
		layout.use_property_decorate = False  # No animation
		
		# Combine all used paths for variable checks
		paths = ''
		if prefs.override_autosave_render:
			paths += prefs.file_location_global
			if prefs.file_name_type_global == 'CUSTOM':
				paths += prefs.file_name_custom_global
		else:
			paths += settings.file_location
			if settings.file_name_type == 'CUSTOM':
				paths += settings.file_name_custom
		
		# Variable list UI
		renderkit_variable_ui(layout, context, paths=paths, postrender=True, noderender=False, autoclose=True, customserial=True)
		
		layout.use_property_split = True
		
		# File location with global override
		if prefs.override_autosave_render:
			override = layout.row()
			override.use_property_split = True
			override.active = False
			override.prop(prefs, 'file_location_global')
			if '{serial}' in prefs.file_location_global:
				override.prop(prefs, "file_serial_global", text="")
		else:
			layout.use_property_split = False
			layout.prop(settings, 'file_location', text="")
			layout.use_property_split = True
			
		# File name with global override
		if prefs.override_autosave_render:
			override = layout.row()
			override.active = False
			override.prop(prefs, 'file_name_type_global', icon='FILE_TEXT')
			if prefs.file_name_type_global == 'CUSTOM':
				override.prop(prefs, "file_name_custom_global", text='')
				if '{serial}' in prefs.file_name_custom_global:
					override.prop(prefs, "file_serial_global", text="")
		else:
			layout.prop(settings, 'file_name_type', icon='FILE_TEXT')
			if settings.file_name_type == 'CUSTOM':
				layout.prop(settings, 'file_name_custom')
				
		# File format with global override
		if prefs.override_autosave_render:
			override = layout.row()
			override.active = False
			override.prop(prefs, 'file_format_global', icon='FILE_IMAGE')
		else:
			layout.prop(settings, 'file_format', icon='FILE_IMAGE')
			
		# Multilayer EXR warning
		if context.scene.render.image_settings.file_format == 'OPEN_EXR_MULTILAYER' and (prefs.file_format_global == 'SCENE' and prefs.override_autosave_render or settings.file_format == 'SCENE' and not prefs.override_autosave_render):
			error = layout.box()
			error.label(text="Python API can only save single layer EXR files")
			error.label(text="Report: https://developer.blender.org/T71087")



class RENDER_PT_autosave_video(bpy.types.Panel):
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_context = "output"
	bl_label = "Autosave Videos"
	bl_parent_id = "RENDER_PT_output"

	@classmethod
	def poll(cls, context):
		prefs = context.preferences.addons[__package__].preferences
		return (
			# Check if FFmpeg processing is enabled
			prefs.ffmpeg_processing
			# Check if the FFmpeg location appears to be valid
			and prefs.ffmpeg_exists
		)
	
	def draw(self, context):
		settings = context.scene.render_kit_settings
		
		layout = self.layout
		layout.use_property_decorate = False  # No animation
		
		# Check if the output format is supported by FFmpeg
		if not context.scene.render.image_settings.file_format in FFMPEG_FORMATS:
			error = layout.box()
			error.label(text='"' + context.scene.render.image_settings.file_format + '" output format is not supported by FFmpeg')
			error.label(text="Supported image formats: " + ', '.join(FFMPEG_FORMATS))
			layout = layout.column()
			layout.active = False
			layout.enabled = False
		
		# Combine all used paths for variable checks
		paths = ''
		paths += settings.autosave_video_prores_location if settings.autosave_video_prores else ''
		paths += settings.autosave_video_mp4_location if settings.autosave_video_prores else ''
		paths += settings.autosave_video_custom_location if settings.autosave_video_prores else ''
		
		# Variable list UI
		renderkit_variable_ui(layout, context, paths=paths, postrender=True, noderender=False, autoclose=True)
		
		
		
		# ProRes alternate UI
		layout.separator()
		row1 = layout.row()
		row1a = row1.row()
		row1a.scale_x = 0.8333
		row1a.prop(settings, 'autosave_video_prores', text='Create ProRes')
		row1b = row1.row(align=True)
		row1b.scale_x = 0.25
		row1b.prop(settings, 'autosave_video_prores_quality', expand=True)
		row2 = layout.row()
		row2.prop(settings, 'autosave_video_prores_location', text='')
		if not settings.autosave_video_prores:
			row1b.active = False
			row1b.enabled = False
			row2.active = False
			row2.enabled = False
		
		# MP4 alternate UI
		layout.separator()
		row1 = layout.row()
		row1a = row1.row()
		row1a.scale_x = 0.8333
		row1a.prop(settings, 'autosave_video_mp4', text='Create MP4')
		row1b = row1.row()
		row1b.prop(settings, 'autosave_video_mp4_quality', slider=True)
		row2 = layout.row()
		row2.prop(settings, 'autosave_video_mp4_location', text='')
		if not settings.autosave_video_mp4:
			row1b.active = False
			row1b.enabled = False
			row2.active = False
			row2.enabled = False
		
		# Custom alternate UI
		layout.separator()
		row1 = layout.row()
		row1a = row1.row()
		row1a.scale_x = 0.8333
		row1a.prop(settings, 'autosave_video_custom', text='Create Custom')
		row1b = row1.row()
		row1b.prop(settings, 'autosave_video_custom_command', text='')
		row2 = layout.row()
		row2.prop(settings, 'autosave_video_custom_location', text='')
		if not settings.autosave_video_custom:
			row1b.active = False
			row1b.enabled = False
			row2.active = False
			row2.enabled = False
			