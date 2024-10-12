import bpy
from .render_variables import OutputVariablePopup

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
		layout.use_property_split = True
		
		
		
		# VARIABLES BAR
		bar = layout.row(align=False)
		
		# Variable list popup button
		ops = bar.operator(OutputVariablePopup.bl_idname, text = "Variable List", icon = "LINENUMBERS_OFF")
		ops.postrender = True
		
		# Local project serial number
		# Global serial number is listed inline with the path or file override, if used
		input = bar.column()
		input.use_property_split = True
		if not (('{serial}' in settings.file_location and not prefs.file_location_override) or (settings.file_name_type == 'CUSTOM' and '{serial}' in settings.file_name_custom and not prefs.file_name_override)):
			input.active = False
			input.enabled = False
		input.prop(settings, 'file_serial', text='serial')
		
		# Local project serial number
		# Global serial number is listed inline with the path or file override, if used
		option = bar.column()
		option.use_property_split = True
		if not (('{marker}' in settings.file_location and not prefs.file_location_override)
			or (settings.file_name_type == 'CUSTOM' and '{marker}' in settings.file_name_custom and not prefs.file_name_override)
			or ('{marker}' in prefs.file_location_global and prefs.file_location_override)
			or (settings.file_name_type == 'CUSTOM' and '{marker}' in prefs.file_name_custom_global and prefs.file_name_override)):
			option.active = False
			option.enabled = False
		option.prop(settings, 'output_marker_direction', text='')
		
		
		
		# File location with global override
		if prefs.file_location_override:
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
		if prefs.file_name_override:
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
		if prefs.file_format_override:
			override = layout.row()
			override.active = False
			override.prop(prefs, 'file_format_global', icon='FILE_IMAGE')
		else:
			layout.prop(settings, 'file_format', icon='FILE_IMAGE')
			
		# Multilayer EXR warning
		if context.scene.render.image_settings.file_format == 'OPEN_EXR_MULTILAYER' and (prefs.file_format_global == 'SCENE' and prefs.file_format_override or settings.file_format == 'SCENE' and not prefs.file_format_override):
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
		
		
		
		# VARIABLES BAR
		bar = layout.row(align=False)
		# Combine all used paths for variable checks
		paths = ''
		paths += settings.autosave_video_prores_location if settings.autosave_video_prores else ''
		paths += settings.autosave_video_mp4_location if settings.autosave_video_prores else ''
		paths += settings.autosave_video_custom_location if settings.autosave_video_prores else ''
		
		# Variable list popup button
		ops = bar.operator(OutputVariablePopup.bl_idname, text = "Variable List", icon = "LINENUMBERS_OFF")
		ops.postrender = True
		
		# Local project serial number
		input = bar.column()
#		input.use_property_split = True
		if not '{serial}' in paths:
			input.active = False
			input.enabled = False
		input.prop(settings, 'output_file_serial', text='serial')
		
		# Local project serial number
		option = bar.column()
#		option.use_property_split = True
		if not '{marker}' in paths:
			option.active = False
			option.enabled = False
		option.prop(settings, 'output_marker_direction', text='')
		
		
		
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
			